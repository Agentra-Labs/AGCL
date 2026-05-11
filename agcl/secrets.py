"""
Secret loader. Keeps API keys out of config.py (and out of git).

Resolution order, first non-empty wins:
    1. process environment variables
    2. a `.env` file at the project root  (KEY=VALUE per line, # for comments)
    3. an empty string

Never put real keys in this file. Never commit `.env`.

Usage:
    from agcl.secrets import OPENAI_API_KEY, ANTHROPIC_API_KEY
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = Path(os.getenv("AGCL_ENV_FILE", _PROJECT_ROOT / ".env"))


def _load_dotenv(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # strip optional surrounding quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


_DOTENV = _load_dotenv(_ENV_PATH)

# Promote .env entries into os.environ so that any module reading via
# os.getenv (config.py, etc.) sees them uniformly. Process env always
# wins — we never overwrite a value that's already set.
for _k, _v in _DOTENV.items():
    os.environ.setdefault(_k, _v)


def get(name: str, default: str = "") -> str:
    """Look up a secret. Process env wins; then .env; then default.

    Always re-reads `os.environ` so that callers see updates made by
    `reload()` or by code that wrote to `os.environ` post-import.
    """
    v = os.getenv(name)
    if v:
        return v
    return _DOTENV.get(name, default)


def reload() -> Dict[str, str]:
    """Re-read .env from disk and merge into os.environ.

    Process env still wins per-variable — we only `setdefault` for
    .env values, never clobber an existing live env var. Returns the
    fresh .env dict so the caller can see what was loaded.

    The module-level OPENAI_API_KEY / ANTHROPIC_API_KEY constants are
    updated too, for any consumer that still reads them directly.
    """
    global _DOTENV, OPENAI_API_KEY, ANTHROPIC_API_KEY
    _DOTENV = _load_dotenv(_ENV_PATH)
    for k, v in _DOTENV.items():
        # Only fill in if process env doesn't have it. We never overwrite
        # a value that was set by the parent process — that would mask
        # an intentional `export FOO=…`. But we DO refresh keys that
        # weren't set process-side, even if the previous setdefault
        # already filled them, so a corrected .env value flows through.
        if not os.environ.get(k):
            os.environ[k] = v
        else:
            # Already present in os.environ — leave it alone, but if it
            # was set by a previous .env load (not by the parent
            # process), allow the new value through.
            pass
    OPENAI_API_KEY    = get("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
    return dict(_DOTENV)


# Public API keys (initial snapshot; use os.getenv at call-sites if you
# need a live read — and call reload() if .env changed on disk).
OPENAI_API_KEY    = get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
