"""
Centralized config bundle — export, import, and validate AGCL setups
across machines.

Goal: a teammate exports `agcl-config.json`, sends it; the recipient
runs `agcl config import agcl-config.json` and gets either:
    - "ready to apply" with everything already on disk, or
    - a list of missing pieces (HF model not downloaded, GGUF file
      absent, env var like ANTHROPIC_API_KEY not set, optional pip
      package not installed) — never a crash.

Bundle schema (JSON):
    {
      "agcl_config_version": 1,
      "exported_at": "<iso8601>",
      "env": { "DEFAULT_CLOUD": "claude", ... },     # whitelisted env vars
      "mas_json": { "agents": [...] },                # the canonical mas.json
      "model_files": [                                # paths the env points to
        {"path": "models/SmolLM2-135M.Q2_K.gguf", "kind": "gguf",
         "size_hint": 73000000, "sha256": "...", "note": "..."}
      ],
      "hf_models": [                                  # transformers cache hits
        {"id": "Qwen/Qwen2.5-0.5B-Instruct"}
      ],
      "extras_required": ["litellm", "redis", "aioboto3"]   # optional toolkit deps
    }
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List, Optional


BUNDLE_VERSION = 1
DEFAULT_PATH = "agcl-config.json"

# Env vars we're willing to put in a shared bundle. **No secrets.**
# Secrets (API keys) are deliberately excluded — recipients set their
# own ANTHROPIC_API_KEY / OPENAI_API_KEY locally.
_EXPORT_ENV = [
    "DEFAULT_CLOUD", "OPENAI_MODEL", "CLAUDE_MODEL",
    "LOCAL_MODEL_PATH", "LOCAL_MODEL_TYPE", "LOCAL_N_CTX",
    "LOCAL_N_GPU_LAYERS", "LOCAL_N_THREADS", "PREFIX_WORD_COUNT",
    "MAS_PATTERN", "MAS_ROUNDS", "MAS_DEVICE", "MAS_DTYPE",
    "STATE_DIR", "IDLE_FLUSH_SEC", "SESSION_TTL_SEC",
    "MAX_CONTEXT_TOKENS", "RECONTEX_KEEP_RECENT",
    "PRESSURE_WINDOW_SEC", "PRESSURE_LIMIT",
    "MIN_PATTERN_DAYS", "PATTERN_LOOKBACK_DAYS",
    "MINI_ENABLED", "MINI_AUTOSTART", "MINI_ARCH", "MINI_HIDDEN",
    "MINI_LAYERS", "MINI_HEADS", "MINI_MAX_SEQ", "MINI_VOCAB",
    "MINI_ATTENTION", "MINI_WINDOW", "MINI_STRATEGY", "MINI_LR",
    "MINI_BUFFER_SIZE", "MINI_BATCH_SIZE", "MINI_YIELD_MS",
    "MINI_NICE", "MINI_CKPT_INTERVAL",
    # toolkit gateway settings (no secrets, just URLs / aliases)
    "AGCL_LLM_BASE_URL", "AGCL_LLM_MODEL",
    "OLLAMA_HOST", "OLLAMA_MODEL",
    "VLLM_HOST", "VLLM_MODEL",
    "TGI_HOST",  "TGI_MODEL",
    "AGCL_REDIS_URL", "AGCL_S3_BUCKET", "AGCL_S3_ENDPOINT",
    "AGCL_RELAY_URL", "AGCL_WEBRTC_ENABLED",
]

# Optional toolkit dependencies and what they unlock. Used by the
# import-time dependency hint: instead of crashing, we tell the user
# exactly what to `pip install` or download.
_OPTIONAL_DEPS = {
    "litellm":      ("litellm",       "AGCL_LLM_BASE_URL"),
    "redis":        ("redis",         "AGCL_REDIS_URL"),
    "aioboto3":     ("aioboto3",      "AGCL_S3_BUCKET"),
    "aiortc":       ("aiortc",        "AGCL_WEBRTC_ENABLED"),
    "discord.py":   ("discord",       "DISCORD_BOT_TOKEN"),
    "slack-bolt":   ("slack_bolt",    "SLACK_BOT_TOKEN"),
    "mcp":          ("mcp",           None),
    "fastmcp":      ("fastmcp",       None),
    "openagents":   ("openagents",    None),
    "transformers": ("transformers",  None),
}


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------

def export(path: Optional[str] = None) -> Dict[str, Any]:
    """Build a bundle dict from the running config + write it to disk."""
    bundle: Dict[str, Any] = {
        "agcl_config_version": BUNDLE_VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "env": {},
        "mas_json": None,
        "model_files": [],
        "hf_models":   [],
        "extras_required": [],
    }

    # 1. env: read live values from current process environment.
    for k in _EXPORT_ENV:
        v = os.environ.get(k)
        if v is not None and v != "":
            bundle["env"][k] = v

    # 2. mas.json
    mp = Path("mas.json")
    if mp.exists():
        try:
            bundle["mas_json"] = json.loads(mp.read_text())
        except json.JSONDecodeError as e:
            bundle["mas_json"] = {"_error": f"invalid mas.json: {e}"}

    # 3. model file pointers
    local = bundle["env"].get("LOCAL_MODEL_PATH") or "models/SmolLM2-135M.Q2_K.gguf"
    p = Path(local)
    if p.exists():
        bundle["model_files"].append({
            "path": str(p),
            "kind": "gguf" if p.suffix.lower() == ".gguf" else "binary",
            "size_hint": p.stat().st_size,
        })

    # 4. HF models implied by mas.json
    if isinstance(bundle["mas_json"], dict):
        for a in bundle["mas_json"].get("agents") or []:
            if a.get("backend") == "hf" and a.get("model"):
                bundle["hf_models"].append({"id": a["model"]})

    # 5. extras: which optional adapters does this bundle depend on?
    for dep_name, (mod, env_key) in _OPTIONAL_DEPS.items():
        if env_key and env_key in bundle["env"]:
            bundle["extras_required"].append(dep_name)

    out = path or DEFAULT_PATH
    Path(out).write_text(json.dumps(bundle, indent=2, sort_keys=False))
    return bundle


# ----------------------------------------------------------------------
# Validation (the hint engine — never crashes; returns a report)
# ----------------------------------------------------------------------

def validate(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inspect a bundle against the local environment. Returns a report:

        {"ok": bool,
         "version_ok": bool,
         "missing_files":   [...],
         "missing_hf":      [...],
         "missing_env":     [...],   # secrets (API keys) the recipient must set
         "missing_extras":  [...],
         "hints": ["pip install X", "ollama pull Y", ...]}
    """
    report: Dict[str, Any] = {
        "ok": True,
        "version_ok": bundle.get("agcl_config_version") == BUNDLE_VERSION,
        "missing_files":  [],
        "missing_hf":     [],
        "missing_env":    [],
        "missing_extras": [],
        "hints":          [],
    }

    if not report["version_ok"]:
        report["ok"] = False
        report["hints"].append(
            f"bundle is v{bundle.get('agcl_config_version')}, "
            f"this AGCL expects v{BUNDLE_VERSION}; "
            f"run `pip install -U agcl` or trim manually."
        )

    # ---- model files ----
    for mf in bundle.get("model_files", []):
        p = Path(mf.get("path", ""))
        if not p.exists():
            report["missing_files"].append(str(p))
            if mf.get("kind") == "gguf":
                report["hints"].append(
                    f"download GGUF model to {p}: try `huggingface-cli download` "
                    f"or check the project's models/ directory")
            else:
                report["hints"].append(f"missing file: {p}")

    # ---- HF models ----
    cache_root = Path(os.environ.get("HF_HOME",
                                       os.path.join("models", "hf"))).resolve()
    for h in bundle.get("hf_models", []):
        model_id = h.get("id", "")
        if not model_id:
            continue
        # transformers' cache layout: models--<org>--<name>
        cache_name = "models--" + model_id.replace("/", "--")
        cache_dir = cache_root / "hub" / cache_name
        if not cache_dir.exists():
            report["missing_hf"].append(model_id)
            report["hints"].append(
                f"prefetch HF model: `huggingface-cli download {model_id}` "
                f"(or `python main.py autoconfig --download`)")

    # ---- secret env vars (NOT in bundle by design) ----
    cloud = bundle.get("env", {}).get("DEFAULT_CLOUD", "claude")
    needs_anthropic = cloud == "claude" or any(
        a.get("backend") == "claude"
        for a in (bundle.get("mas_json", {}) or {}).get("agents", []) or []
    )
    needs_openai = cloud == "openai"
    if needs_anthropic and not os.environ.get("ANTHROPIC_API_KEY"):
        report["missing_env"].append("ANTHROPIC_API_KEY")
        report["hints"].append("add ANTHROPIC_API_KEY=sk-... to your local .env")
    if needs_openai and not os.environ.get("OPENAI_API_KEY"):
        report["missing_env"].append("OPENAI_API_KEY")
        report["hints"].append("add OPENAI_API_KEY=sk-... to your local .env")

    # ---- toolkit-implied secrets ----
    if bundle.get("env", {}).get("DISCORD_BOT_TOKEN") and \
            not os.environ.get("DISCORD_BOT_TOKEN"):
        report["missing_env"].append("DISCORD_BOT_TOKEN")

    # ---- optional pip extras ----
    for dep in bundle.get("extras_required", []):
        spec = _OPTIONAL_DEPS.get(dep)
        if spec is None:
            continue
        mod_name, _env = spec
        if find_spec(mod_name) is None:
            report["missing_extras"].append(dep)
            report["hints"].append(f"pip install {dep}")

    if (report["missing_files"] or report["missing_hf"] or
            report["missing_env"] or report["missing_extras"]):
        report["ok"] = False
    return report


# ----------------------------------------------------------------------
# Import (write env + mas.json after validation, with --apply gate)
# ----------------------------------------------------------------------

def import_bundle(path: str, apply: bool = False) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"bundle not found: {p}"}
    try:
        bundle = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"bundle is not valid JSON: {e}"}

    report = validate(bundle)
    report["dry_run"] = not apply
    report["bundle"] = {"exported_at": bundle.get("exported_at"),
                         "version":     bundle.get("agcl_config_version")}

    if not apply:
        return report

    # Apply: write .env updates and mas.json (backing up the previous one).
    env_path = Path(".env")
    existing_env = _read_env_file(env_path)
    for k, v in bundle.get("env", {}).items():
        existing_env[k] = str(v)
    _write_env_file(env_path, existing_env)
    report["env_written"] = True

    mas = bundle.get("mas_json")
    if isinstance(mas, dict) and "agents" in mas:
        target = Path("mas.json")
        if target.exists():
            shutil.copy2(target, target.with_suffix(".json.bak"))
        target.write_text(json.dumps(mas, indent=2))
        report["mas_written"] = True

    return report


def _read_env_file(p: Path) -> Dict[str, str]:
    if not p.exists():
        return {}
    out: Dict[str, str] = {}
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v
    return out


def _write_env_file(p: Path, kv: Dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in kv.items()]
    p.write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------------
# CLI entry
# ----------------------------------------------------------------------

def cli(action: str = "show", path: Optional[str] = None,
        apply: bool = False) -> int:
    if action == "export":
        out = path or DEFAULT_PATH
        bundle = export(out)
        print(f"  wrote {out}")
        print(f"  env keys:  {len(bundle['env'])}")
        print(f"  mas:       {bool(bundle['mas_json'])}")
        print(f"  models:    {len(bundle['model_files'])}")
        print(f"  hf models: {len(bundle['hf_models'])}")
        print(f"  extras:    {bundle['extras_required'] or 'none'}")
        return 0

    if action == "show":
        bundle = export(path or "/tmp/agcl-config-show.json")
        os.unlink("/tmp/agcl-config-show.json")
        print(json.dumps(bundle, indent=2))
        return 0

    if action in ("hint", "import"):
        target = path or DEFAULT_PATH
        report = import_bundle(target, apply=apply and action == "import")
        if "error" in report:
            print(f"  error: {report['error']}", file=sys.stderr)
            return 1
        _print_report(report)
        return 0 if report["ok"] else 1

    print(f"unknown action: {action}", file=sys.stderr)
    return 2


def _print_report(report: Dict[str, Any]) -> None:
    if report["dry_run"]:
        print("  [dry run — no files written; pass --apply to actually import]")
    print(f"  bundle version  {report['bundle'].get('version')}  "
          f"exported  {report['bundle'].get('exported_at')}")
    print(f"  status:         {'ready' if report['ok'] else 'incomplete'}")
    if report["missing_files"]:
        print("  missing model files:")
        for f in report["missing_files"]:
            print(f"    - {f}")
    if report["missing_hf"]:
        print("  HF models not in cache:")
        for m in report["missing_hf"]:
            print(f"    - {m}")
    if report["missing_env"]:
        print("  required env vars not set (likely API keys):")
        for v in report["missing_env"]:
            print(f"    - {v}")
    if report["missing_extras"]:
        print("  optional pip packages not installed:")
        for p in report["missing_extras"]:
            print(f"    - {p}")
    if report["hints"]:
        print("  next steps:")
        for h in report["hints"]:
            print(f"    > {h}")
    if report.get("env_written") or report.get("mas_written"):
        print("  applied:")
        if report.get("env_written"): print("    .env updated")
        if report.get("mas_written"): print("    mas.json updated (backup at mas.json.bak)")
