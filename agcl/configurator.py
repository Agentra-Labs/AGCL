"""
Interactive RecursiveMAS configurator.

A keyboardable wizard that walks the user through building an
`mas.json` by hand instead of relying on `autoconfig`'s canonical pair.

Flow:
  1. choose collaboration pattern        (sequential|moe|distill|deliberation|custom)
  2. choose number of rounds             (>=1; deliberation auto-bumps to >=3)
  3. for each agent slot, ask:
        backend          (hf | gguf)
        model            (HF id, local path, or .gguf path)
        role             (free-form; pattern-aware default suggested)
        device + dtype   (hf-only; cpu/cuda + float32/float16/bfloat16)
        n_ctx/n_gpu/n_threads (gguf-only)
  4. optional: download HF models locally to models/hf_local/<slug>/ and
     point the spec at that local path (so subsequent runs do not hit
     the HF Hub at all)
  5. write mas.json (with backup) and optionally update .env to set
     MAS_CONFIG_FILE / MAS_PATTERN / MAS_ROUNDS

Pure stdlib I/O — no extra deps. Works as `python main.py --config` or
`python -m agcl.configurator`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_MODELS_DIR = PROJECT_ROOT / "models" / "hf_local"

PATTERNS = ("sequential", "moe", "distill", "deliberation", "custom")
PATTERN_BLURBS = {
    "sequential":   "fixed pipeline, e.g. planner -> critic -> solver",
    "moe":          "mixture-of-experts; each agent contributes per round",
    "distill":      "exactly 2 agents: teacher -> student",
    "deliberation": ">=2 agents, debate over >=3 rounds",
    "custom":       "free-form; you assign every role yourself",
}
PATTERN_DEFAULT_ROLES = {
    "sequential":   ["planner", "critic", "solver"],
    "moe":          ["expert_0", "expert_1", "expert_2"],
    "distill":      ["teacher", "student"],
    "deliberation": ["agent_0", "agent_1", "agent_2"],
    "custom":       ["agent_0", "agent_1"],
}


# ---------- I/O helpers ----------

def _hr():       print("-" * 56)
def _ok(m: str): print(f"  [ok]   {m}")
def _warn(m):    print(f"  [!!]   {m}")
def _info(m):    print(f"  [..]   {m}")


def _ask(prompt: str, default: Optional[str] = None,
         choices: Optional[List[str]] = None) -> str:
    """Prompt the user; allow empty to take default."""
    suffix = ""
    if choices:
        suffix += f" ({'|'.join(choices)})"
    if default is not None:
        suffix += f" [{default}]"
    while True:
        try:
            ans = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[abort] cancelled by user"); sys.exit(1)
        if not ans and default is not None:
            return default
        if not ans:
            print("  required.")
            continue
        if choices and ans not in choices:
            print(f"  must be one of: {', '.join(choices)}")
            continue
        return ans


def _ask_int(prompt: str, default: int, lo: int = 1, hi: int = 32) -> int:
    while True:
        s = _ask(prompt, default=str(default))
        try:
            v = int(s)
        except ValueError:
            print("  must be an integer.");  continue
        if v < lo or v > hi:
            print(f"  must be in [{lo}, {hi}].");  continue
        return v


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    s = _ask(prompt, default=d, choices=["y", "n"])
    return s == "y"


# ---------- per-agent prompts ----------

def _slug(model_id: str) -> str:
    """Filesystem-safe directory name for a HF model id."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_id).strip("_") or "model"


def _ask_agent(idx: int, pattern: str, total: int) -> Dict[str, Any]:
    print()
    _hr()
    print(f"  agent {idx + 1} of {total}")
    _hr()

    backend = _ask("  backend", default="hf", choices=["hf", "gguf"])

    if backend == "hf":
        model = _ask("  HF model id (or local path)",
                     default="Qwen/Qwen2.5-0.5B-Instruct")
    else:
        model = _ask("  path to .gguf file",
                     default="models/SmolLM2-135M.Q2_K.gguf")

    default_role = (PATTERN_DEFAULT_ROLES.get(pattern, []) + [f"agent_{idx}"])[
        min(idx, len(PATTERN_DEFAULT_ROLES.get(pattern, [])))
    ] if idx < len(PATTERN_DEFAULT_ROLES.get(pattern, [])) else f"agent_{idx}"
    role = _ask("  role (free-form)", default=default_role)

    spec: Dict[str, Any] = {"backend": backend, "model": model, "role": role}

    if backend == "hf":
        spec["device"] = _ask("  device", default="cpu",
                               choices=["cpu", "cuda"])
        spec["dtype"]  = _ask("  dtype",
                               default="float32",
                               choices=["float32", "float16", "bfloat16"])
    else:
        spec["n_ctx"]        = _ask_int("  n_ctx",        default=2048,
                                         lo=128, hi=131072)
        spec["n_gpu_layers"] = _ask_int("  n_gpu_layers", default=0,
                                         lo=0, hi=999)
        spec["n_threads"]    = _ask_int("  n_threads",    default=4,
                                         lo=1, hi=256)

    return spec


# ---------- model snapshot ----------

def _download_snapshot(model_id: str) -> Optional[Path]:
    """
    Pull the full HF repo into models/hf_local/<slug>/ and return that path.
    Returns None on failure (caller falls back to using the original id,
    which lets transformers stream from the HF cache as before).
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        _warn("huggingface_hub not installed; cannot snapshot. "
              "pip install huggingface_hub")
        return None

    LOCAL_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    target = LOCAL_MODELS_DIR / _slug(model_id)

    if target.exists() and any(target.iterdir()):
        _info(f"snapshot already on disk: {target} (reusing)")
        return target

    _info(f"downloading {model_id} -> {target} (one-time)...")
    try:
        snapshot_download(
            repo_id=model_id,
            local_dir=str(target),
            local_dir_use_symlinks=False,
            ignore_patterns=["*.msgpack", "*.h5", "*.ot"],  # skip TF/Flax variants
        )
    except Exception as e:
        _warn(f"snapshot failed ({type(e).__name__}: {e}); "
              f"will leave model id as-is")
        # leave a partial dir behind? clean it.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return None
    _ok(f"snapshot ready at {target}")
    return target


def _maybe_localize(specs: List[Dict[str, Any]]) -> None:
    """Mutates specs in place, swapping HF model ids for local paths."""
    hf_specs = [s for s in specs if s["backend"] == "hf"
                and not Path(s["model"]).exists()]
    if not hf_specs:
        return
    print()
    _hr()
    print("  download HF models locally?")
    print(f"  one-time download into {LOCAL_MODELS_DIR.relative_to(PROJECT_ROOT)}/")
    print("  subsequent runs will not hit the HF Hub at all.")
    _hr()
    if not _ask_yes_no("  download all HF models now?", default=False):
        return
    for s in hf_specs:
        path = _download_snapshot(s["model"])
        if path is not None:
            s["model"] = str(path)


# ---------- write outputs ----------

def _write_mas_json(specs: List[Dict[str, Any]], force: bool) -> Path:
    p = PROJECT_ROOT / "mas.json"
    if p.exists() and not force:
        backup = p.with_suffix(".json.bak")
        shutil.copy(p, backup)
        _info(f"existing mas.json backed up to {backup.name}")
    with p.open("w") as f:
        json.dump({"agents": specs}, f, indent=2)
    _ok(f"wrote {p.relative_to(PROJECT_ROOT)} ({len(specs)} agents)")
    return p


def _patch_env(pattern: str, rounds: int) -> None:
    p = PROJECT_ROOT / ".env"
    existing = {}
    lines: List[str] = []
    if p.exists():
        for line in p.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()
            lines.append(line)

    additions = {}
    for k, v in (
        ("MAS_CONFIG_FILE", "./mas.json"),
        ("MAS_PATTERN",     pattern),
        ("MAS_ROUNDS",      str(rounds)),
    ):
        if k not in existing:
            additions[k] = v

    if not additions:
        _info(".env already has MAS_* keys; not touched")
        return

    if lines and lines[-1].strip():
        lines.append("")
    lines.append("# added by agcl configurator")
    for k, v in additions.items():
        lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n")
    _ok(f"patched .env with {', '.join(additions)}")


# ---------- driver ----------

def run() -> int:
    print()
    _hr()
    print("  agcl — interactive RecursiveMAS configurator")
    _hr()
    print("  press ctrl+c at any time to abort.")

    print()
    print("  patterns:")
    for p in PATTERNS:
        print(f"    {p:13s} {PATTERN_BLURBS[p]}")
    pattern = _ask("\n  pattern", default="sequential", choices=list(PATTERNS))

    if pattern == "distill":
        n_agents = 2
        _info("distill is fixed to 2 agents (teacher, student)")
    else:
        floor = 2 if pattern in ("moe", "deliberation") else 1
        n_agents = _ask_int("\n  number of agents", default=2, lo=floor, hi=12)

    rounds = _ask_int("  number of rounds (loop unrolls)", default=2, lo=1, hi=12)
    if pattern == "deliberation" and rounds < 3:
        _warn("deliberation auto-bumped to 3 rounds")
        rounds = 3

    specs: List[Dict[str, Any]] = []
    for i in range(n_agents):
        specs.append(_ask_agent(i, pattern, n_agents))

    print()
    _hr()
    print("  summary")
    _hr()
    print(f"  pattern: {pattern}   rounds: {rounds}   agents: {len(specs)}")
    for i, s in enumerate(specs):
        bits = [f"backend={s['backend']}", f"role={s['role']}",
                f"model={s['model']}"]
        print(f"    [{i}] " + "  ".join(bits))

    if not _ask_yes_no("\n  proceed?", default=True):
        print("[abort] not writing files."); return 1

    _maybe_localize(specs)

    force = _ask_yes_no("\n  overwrite mas.json without backup?", default=False)
    _write_mas_json(specs, force=force)

    if _ask_yes_no("  also patch .env with MAS_PATTERN / MAS_ROUNDS / MAS_CONFIG_FILE?",
                   default=True):
        _patch_env(pattern, rounds)

    print()
    _hr()
    _ok("done. test with:  python main.py recursive info")
    _hr()
    return 0


if __name__ == "__main__":
    sys.exit(run())
