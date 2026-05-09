# Docker — containerized AGCL

<p align="left">
  <img src="../assets/docker.svg" width="22" alt="Docker" />&nbsp;
  <a href="https://img.shields.io/badge/compose-v2-2496ED"><img alt="Compose" src="https://img.shields.io/badge/compose-v2-2496ED"></a>
  <a href="https://img.shields.io/badge/buildx-multi--arch-2496ED"><img alt="buildx" src="https://img.shields.io/badge/buildx-multi--arch-2496ED"></a>
</p>

Build, run, and ship AGCL as a container. This is the recommended path
for any deployment that isn't a single user's laptop.

> Part of the **[Agent-platform integrations](../integrations.md)**.
> For Kubernetes / Helm, see [cloud/k8s.md](cloud/k8s.md).

> **The repo doesn't ship a committed `Dockerfile`** — it's generated
> on demand so it stays in sync with whatever AGCL config you've got.
> Two equivalent paths:
>
> ```bash
> python main.py toolkit emit-docker            # CPU image
> python main.py toolkit emit-docker --gpu      # CUDA base
> ```
>
> Outputs `deploy/Dockerfile`, `deploy/Dockerfile.gpu`,
> `deploy/docker-compose.yml`, `deploy/docker-compose.gpu.yml`,
> `deploy/.dockerignore`, and `deploy/litellm_config.yaml`. The emitter
> source is [`agcl/toolkit/docker.py`](../../agcl/toolkit/docker.py).
> The Dockerfile / Compose YAML below are exactly what gets emitted —
> use `emit-docker` if you'd rather not copy by hand.

---

## Dockerfile (what `emit-docker` writes)

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# system deps (only what llama.cpp / sentencepiece need)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# directories that should survive container restarts
RUN mkdir -p /data/agent_state /data/models
ENV STATE_DIR=/data/agent_state \
    LOCAL_MODEL_PATH=/data/models/SmolLM2-135M.Q2_K.gguf

EXPOSE 9876
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:9876/node/health || exit 1

CMD ["python", "main.py", "node", "--bind", "0.0.0.0", "--port", "9876"]
```

---

## Build & run

```bash
# build
docker build -t agcl:latest .

# run (CPU only, persisting state to a host directory)
docker run -d --name agcl \
  -p 9876:9876 \
  -v $PWD/agent_state:/data/agent_state \
  -v $PWD/models:/data/models \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e AGCL_NODE_AUTH=$AGCL_NODE_AUTH \
  agcl:latest

# tail the auth-key banner
docker logs -f agcl
```

### GPU build

For local-model GPU acceleration, swap the base image and rebuild
`llama-cpp-python` with CUDA:

```dockerfile
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y python3 python3-pip
ENV CMAKE_ARGS="-DLLAMA_CUDA=on"
RUN pip install llama-cpp-python --force-reinstall --no-cache-dir
# ... rest of the same Dockerfile
```

Run with:

```bash
docker run -d --gpus all -p 9876:9876 ... agcl:latest
```

---

## docker-compose.yml (full stack)

This brings up AGCL alongside Redis (distributed session state),
LiteLLM (provider abstraction), and Ollama (local inference). Each
piece is documented in its own cloud guide.

```yaml
# docker-compose.yml
version: "3.9"

services:
  agcl:
    build: .
    ports: ["9876:9876"]
    environment:
      - AGCL_REDIS_URL=redis://redis:6379
      - AGCL_LLM_BASE_URL=http://litellm:4000
      - AGCL_LLM_API_KEY=${AGCL_LLM_API_KEY:-sk-agcl-internal}
      - STATE_DIR=/data/agent_state
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - agcl-state:/data/agent_state
      - ./models:/data/models
    depends_on: [redis, litellm]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9876/node/health"]
      interval: 30s
      retries: 3

  redis:
    image: valkey/valkey:latest
    command: ["valkey-server", "--appendonly", "yes"]
    volumes: [redis-data:/data]

  litellm:
    image: ghcr.io/berriai/litellm:main-stable
    volumes:
      - ./litellm_config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    ports: ["4000:4000"]
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - GROQ_API_KEY=${GROQ_API_KEY}

  ollama:
    image: ollama/ollama:latest
    volumes: [ollama-data:/root/.ollama]
    ports: ["11434:11434"]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  agcl-state:
  redis-data:
  ollama-data:
```

```bash
docker compose up -d
docker compose logs -f agcl
```

Each backing service has its own page:

- [LiteLLM](cloud/litellm.md)
- [Ollama](cloud/ollama.md)
- [Redis / Valkey](cloud/redis.md)
- [Kubernetes](cloud/k8s.md) (if you outgrow Compose)

---

## Multi-arch image (`linux/amd64` + `linux/arm64`)

```bash
docker buildx create --name agcl-builder --use
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/yourorg/agcl:latest \
  --push .
```

---

## Image tagging convention

| Tag | Meaning |
|---|---|
| `:latest` | Last release on `main` |
| `:vX.Y.Z` | Pinned release |
| `:cpu` / `:gpu-cuda12` | Variant suffixes for the same release |
| `:dev` | Built off the current branch's HEAD (don't pin to this) |

---

## Common gotchas

- The auth key is printed to **stdout** on first start. Either capture
  it from `docker logs` or set `AGCL_NODE_AUTH=$STABLE_KEY` (which
  `python main.py node` reads) so it survives container restarts.
- Bind to `0.0.0.0` inside the container — the published port maps to
  the host.
- The `.env` file is *not* baked into the image; pass secrets via
  `-e` / `--env-file` or a Compose secret. Never commit `.env`.
- `STATE_DIR` should always be a mounted volume; otherwise the
  trained-topic checkpoints disappear on container replacement.
