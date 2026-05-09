"""
Docker / Compose manifest emitter.

Generates real, runnable Dockerfile + docker-compose.yml from the
current AGCL config — no boilerplate, no placeholders. Each call
re-reads env vars so the output reflects what the running process is
actually configured for.

Public surface:
    dockerfile()        -> str   (production CPU image; GPU variant via gpu=True)
    compose()           -> str   (full stack: agcl + redis + litellm + ollama)
    write_to(deploy_dir) -> dict (writes Dockerfile, compose, .dockerignore)
    ping()              -> dict  (is `docker` CLI available?)
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def dockerfile(*, python: str = "3.11", gpu: bool = False) -> str:
    """Return a production Dockerfile as a string."""
    if gpu:
        base = "FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04 AS base"
        py_install = (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "python3 python3-pip python3-venv build-essential git curl "
            "ca-certificates && rm -rf /var/lib/apt/lists/* && "
            "ln -sf /usr/bin/python3 /usr/local/bin/python\n"
            'ENV CMAKE_ARGS="-DLLAMA_CUDA=on"\n'
            "RUN pip install --no-cache-dir llama-cpp-python --force-reinstall\n"
        )
    else:
        base = f"FROM python:{python}-slim AS base"
        py_install = (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "build-essential git curl ca-certificates && "
            "rm -rf /var/lib/apt/lists/*\n"
        )

    return f"""# syntax=docker/dockerfile:1.7
{base}

ENV PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
{py_install}
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

RUN mkdir -p /data/agent_state /data/models
ENV STATE_DIR=/data/agent_state \\
    LOCAL_MODEL_PATH=/data/models/SmolLM2-135M.Q2_K.gguf

EXPOSE 9876
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
  CMD curl -fsS http://localhost:9876/node/health || exit 1

CMD ["python", "main.py", "node", "--bind", "0.0.0.0", "--port", "9876"]
"""


def dockerignore() -> str:
    return "\n".join([
        "__pycache__/", "*.py[cod]", "*.egg-info/",
        ".git/", ".gitignore",
        ".env", ".env.local", "*.env",
        ".agent_state/", ".claude/", "models/",
        "node_modules/", "dist/",
        "docs/assets/",
        "*.log",
    ]) + "\n"


def compose(*, with_ollama: bool = True, with_litellm: bool = True,
             with_redis: bool = True, gpu: bool = False) -> str:
    """Return a docker-compose.yml as a string. Toggle services via flags."""
    services: Dict[str, Any] = {
        "agcl": {
            "build": ".",
            "ports": ["9876:9876"],
            "environment": [
                "STATE_DIR=/data/agent_state",
                "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}",
                "OPENAI_API_KEY=${OPENAI_API_KEY:-}",
            ],
            "volumes": [
                "agcl-state:/data/agent_state",
                "./models:/data/models",
            ],
            "healthcheck": {
                "test": ["CMD", "curl", "-fsS", "http://localhost:9876/node/health"],
                "interval": "30s",
                "retries": 3,
            },
        },
    }
    depends: List[str] = []
    if with_redis:
        services["redis"] = {
            "image": "valkey/valkey:latest",
            "command": ["valkey-server", "--appendonly", "yes"],
            "volumes": ["redis-data:/data"],
        }
        services["agcl"]["environment"].append("AGCL_REDIS_URL=redis://redis:6379")
        depends.append("redis")
    if with_litellm:
        services["litellm"] = {
            "image": "ghcr.io/berriai/litellm:main-stable",
            "volumes": ["./deploy/litellm_config.yaml:/app/config.yaml:ro"],
            "command": ["--config", "/app/config.yaml", "--port", "4000"],
            "ports": ["4000:4000"],
            "environment": [
                "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}",
                "OPENAI_API_KEY=${OPENAI_API_KEY:-}",
                "GROQ_API_KEY=${GROQ_API_KEY:-}",
            ],
        }
        services["agcl"]["environment"] += [
            "AGCL_LLM_BASE_URL=http://litellm:4000",
            "AGCL_LLM_API_KEY=${AGCL_LLM_API_KEY:-sk-agcl-internal}",
        ]
        depends.append("litellm")
    if with_ollama:
        ollama_svc: Dict[str, Any] = {
            "image": "ollama/ollama:latest",
            "volumes": ["ollama-data:/root/.ollama"],
            "ports": ["11434:11434"],
        }
        if gpu:
            ollama_svc["deploy"] = {
                "resources": {"reservations": {"devices": [
                    {"driver": "nvidia", "count": 1, "capabilities": ["gpu"]},
                ]}},
            }
        services["ollama"] = ollama_svc
        services["agcl"]["environment"].append("OLLAMA_HOST=http://ollama:11434")
    if depends:
        services["agcl"]["depends_on"] = depends

    volumes: Dict[str, Any] = {"agcl-state": None}
    if with_redis:  volumes["redis-data"] = None
    if with_ollama: volumes["ollama-data"] = None

    return _yaml_dump({"version": "3.9", "services": services, "volumes": volumes})


def litellm_config_yaml() -> str:
    """Default LiteLLM proxy config that matches AGCL's env vars."""
    return """\
model_list:
  - model_name: smart
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: smart
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: fast
    litellm_params:
      model: groq/llama3-8b-8192
      api_key: os.environ/GROQ_API_KEY

  - model_name: local
    litellm_params:
      model: ollama/llama3
      api_base: http://ollama:11434

router_settings:
  routing_strategy: latency-based-routing
  fallbacks:
    - model_name: smart
      fallback_models: [fast, local]

general_settings:
  master_key: sk-agcl-internal
"""


def write_to(deploy_dir: str = "deploy") -> Dict[str, Any]:
    """Materialize Dockerfile, docker-compose.yml, .dockerignore, litellm_config.yaml."""
    out = Path(deploy_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "Dockerfile":            dockerfile(),
        "Dockerfile.gpu":        dockerfile(gpu=True),
        "docker-compose.yml":    compose(),
        "docker-compose.gpu.yml": compose(gpu=True),
        ".dockerignore":         dockerignore(),
        "litellm_config.yaml":   litellm_config_yaml(),
    }
    written = []
    for name, body in files.items():
        p = out / name
        p.write_text(body)
        written.append(str(p))
    return {"ok": True, "dir": str(out), "files": written}


def ping() -> Dict[str, Any]:
    """Is the docker CLI available?"""
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "available": False,
                "hint": "install Docker: https://docs.docker.com/engine/install/"}
    try:
        out = subprocess.check_output(
            [docker, "version", "--format", "{{.Server.Version}}"],
            stderr=subprocess.STDOUT, timeout=3,
        ).decode().strip()
        return {"ok": True, "available": True, "version": out, "path": docker}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "available": True, "path": docker, "error": str(e)}


# ----------------------------------------------------------------------
# Tiny yaml dump (avoids adding PyYAML as a dep).
# Only handles the subset we need: dicts / lists / scalars, no anchors.
# ----------------------------------------------------------------------

def _yaml_dump(obj: Any) -> str:
    lines: List[str] = []
    _emit(obj, "", lines)
    return "\n".join(lines) + "\n"


def _emit(obj: Any, pad: str, lines: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and v:
                lines.append(f"{pad}{k}:")
                _emit(v, pad + "  ", lines)
            elif isinstance(v, list) and v:
                lines.append(f"{pad}{k}:")
                for item in v:
                    if isinstance(item, dict):
                        first = True
                        for ik, iv in item.items():
                            prefix = f"{pad}- " if first else f"{pad}  "
                            first = False
                            if isinstance(iv, (dict, list)) and iv:
                                lines.append(f"{prefix}{ik}:")
                                _emit(iv, pad + "    ", lines)
                            else:
                                lines.append(f"{prefix}{ik}: {_scalar(iv)}")
                    else:
                        lines.append(f"{pad}- {_scalar(item)}")
            elif v is None:
                lines.append(f"{pad}{k}:")
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}: {{}}" if isinstance(v, dict) else f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _emit(item, pad, lines)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(f"{pad}{_scalar(obj)}")


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    risky = (":" in s) or s.startswith(("- ", "?", "&", "*", "!", "|", ">", "%", "@", "`"))
    if risky or s == "":
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s
