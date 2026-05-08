"""
One-shot project setup so a fresh checkout is ready to run RecursiveMAS
with the canonical HF agent pair (Qwen2.5-0.5B + TinyLlama-1.1B).

Idempotent. Each step prints what it did or skipped, and never
clobbers user-customized files unless --force is set.

Steps:
    1. mas.json           — canonical 2-agent HF config at project root
    2. .env               — append missing MAS_* keys (keeps your API keys)
    3. models/hf/         — project-local HF cache directory
    4. transformers       — verify installed (with --install-deps, install)
    5. framework self-test — 21 unit tests
    6. (--download)       — pre-pull both HF models into models/hf/

Run via either:
    python main.py autoconfig
    python main.py --autoconfig
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# Canonical config: two HF agents, sequential pattern, 2 rounds.
# Hidden sizes 896 (Qwen) + 2048 (TinyLlama) exercise the OuterLink's
# dim-mismatch path meaningfully. Both models are open and ungated.
CANONICAL_MAS_JSON = {
    "agents": [
        {
            "backend": "hf",
            "model": "Qwen/Qwen2.5-0.5B-Instruct",
            "role": "planner",
            "device": "cpu",
            "dtype": "float32",
        },
        {
            "backend": "hf",
            "model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "role": "solver",
            "device": "cpu",
            "dtype": "float32",
        },
    ],
}


DEFAULT_ENV_KEYS = {
    "MAS_CONFIG_FILE": "./mas.json",
    "MAS_PATTERN":     "sequential",
    "MAS_ROUNDS":      "2",
}


# ---------- output helpers ----------

def _hdr(num: int, total: int, msg: str):
    print(f"\n[{num}/{total}] {msg}")

def _ok(msg: str):   print(f"  [ok]   {msg}")
def _skip(msg: str): print(f"  [skip] {msg}")
def _warn(msg: str): print(f"  [!!]   {msg}")


# ---------- individual steps ----------

def ensure_mas_json(force: bool = False) -> Path:
    p = PROJECT_ROOT / "mas.json"
    if p.exists() and not force:
        try:
            data = json.loads(p.read_text())
            n = len(data.get("agents", []))
            _skip(f"mas.json exists ({n} agents) — leaving alone (use --force to overwrite)")
            return p
        except json.JSONDecodeError:
            _warn("mas.json exists but is invalid JSON — overwriting")
    p.write_text(json.dumps(CANONICAL_MAS_JSON, indent=2) + "\n")
    _ok(f"wrote {p.relative_to(PROJECT_ROOT)} (Qwen2.5-0.5B planner + TinyLlama-1.1B solver)")
    return p


def ensure_env_file() -> Path:
    env = PROJECT_ROOT / ".env"
    example = PROJECT_ROOT / ".env.example"

    if not env.exists():
        if example.exists():
            shutil.copy(example, env)
            _ok("created .env from .env.example")
        else:
            env.write_text("# agcl env file\n")
            _ok("created empty .env")

    text = env.read_text()
    existing_keys = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        existing_keys.add(s.split("=", 1)[0].strip())

    missing = {k: v for k, v in DEFAULT_ENV_KEYS.items() if k not in existing_keys}
    if not missing:
        _skip("all MAS_* keys already present in .env")
        return env

    with env.open("a") as f:
        if not text.endswith("\n"):
            f.write("\n")
        f.write("\n# RecursiveMAS — auto-added by autoconfig\n")
        for k, v in missing.items():
            f.write(f"{k}={v}\n")
    _ok(f"added to .env: {', '.join(missing.keys())}")
    return env


def ensure_models_dir() -> Path:
    d = PROJECT_ROOT / "models" / "hf"
    if d.exists():
        _skip(f"{d.relative_to(PROJECT_ROOT)} already exists")
    else:
        d.mkdir(parents=True, exist_ok=True)
        _ok(f"created {d.relative_to(PROJECT_ROOT)}")
    return d


def check_transformers(install: bool = False) -> bool:
    try:
        import transformers  # noqa: F401
        _ok(f"transformers installed (version {transformers.__version__})")
        return True
    except ImportError:
        if not install:
            _warn("transformers not installed")
            print("         re-run with --install-deps, or:  pip install transformers")
            return False
        print("         transformers not installed; installing now...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "transformers"],
            capture_output=False,
        )
        if r.returncode != 0:
            _warn("pip install transformers failed")
            return False
        # re-import
        try:
            import transformers as _t
            _ok(f"transformers installed (version {_t.__version__})")
            return True
        except ImportError:
            _warn("transformers still not importable after install")
            return False


def run_framework_selftest() -> bool:
    from agcl.recursive import validate as rv
    ok = rv.run_all(verbose=False)
    if ok:
        _ok(f"{len(rv.TESTS)}/{len(rv.TESTS)} framework tests passed")
    else:
        _warn("framework self-test reported failures")
        print("         re-run with details:  python main.py recursive validate")
    return ok


def download_models() -> bool:
    """Trigger HF downloads by building the MAS — first call pulls weights."""
    try:
        from agcl.recursive import build_from_config
    except ImportError as e:
        _warn(f"agcl.recursive not importable: {e}")
        return False
    print("         (this can take several minutes on first run)")
    try:
        mas = build_from_config()
        dims = mas.dims
        _ok(f"loaded {len(mas.agents)} agents, hidden sizes={dims}")
        return True
    except Exception as e:
        _warn(f"download/load failed: {type(e).__name__}: {e}")
        return False


# ---------- public entry ----------

def run(force: bool = False, install_deps: bool = False,
        download: bool = False) -> int:
    print("agcl autoconfig — preparing project for HF RecursiveMAS")
    print(f"  project root: {PROJECT_ROOT}")

    total = 6 if download else 5

    _hdr(1, total, "writing mas.json (HF agent config)")
    ensure_mas_json(force=force)

    _hdr(2, total, "configuring .env")
    ensure_env_file()

    _hdr(3, total, "creating models/hf/ directory (project-local HF cache)")
    ensure_models_dir()

    _hdr(4, total, "checking transformers dependency")
    have_tx = check_transformers(install=install_deps)

    _hdr(5, total, "running framework self-test (21 unit tests)")
    selftest_ok = run_framework_selftest()

    if download and have_tx:
        _hdr(6, total, "pre-downloading HF models into models/hf/")
        download_models()
    elif download and not have_tx:
        _hdr(6, total, "pre-downloading HF models into models/hf/")
        _warn("skipped — transformers not installed")

    print("\n" + "=" * 70)
    print("  autoconfig complete" + ("" if selftest_ok else " (with warnings)"))
    print("=" * 70)
    print("\nnext steps:")
    if not have_tx:
        print("  pip install transformers                       # required for HF agents")
    print("  python main.py recursive info                  # show resolved config")
    print('  python main.py recursive run "your prompt"     # generate text')
    print("  python scripts/validate_hf_training.py         # full inference + training")
    return 0 if selftest_ok else 1
