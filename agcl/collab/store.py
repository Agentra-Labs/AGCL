"""
agcl.collab.store — simple JSON-file persistence for collaboration data.

Mirrors the LocalStateStore pattern: one JSON file per logical key,
stored under <STATE_DIR>/collab/. No external dependencies.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()


def _root() -> Path:
    from agcl import config as cfg
    p = Path(getattr(cfg, "STATE_DIR", ".agent_state")) / "collab"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in key)


def _path(key: str) -> Path:
    return _root() / f"{_safe(key)}.json"


def kv_get(key: str) -> Optional[Any]:
    p = _path(key)
    if not p.exists():
        return None
    with _lock:
        return json.loads(p.read_text())


def kv_set(key: str, value: Any) -> None:
    with _lock:
        _path(key).write_text(json.dumps(value, default=str))


def kv_delete(key: str) -> bool:
    p = _path(key)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


def list_append(key: str, item: Any) -> None:
    """Append item to a JSON list stored at key."""
    with _lock:
        p = _path(key)
        items: list = json.loads(p.read_text()) if p.exists() else []
        items.append(item)
        p.write_text(json.dumps(items, default=str))


def list_get(key: str) -> list:
    p = _path(key)
    if not p.exists():
        return []
    with _lock:
        return json.loads(p.read_text())


def list_replace(key: str, items: list) -> None:
    with _lock:
        _path(key).write_text(json.dumps(items, default=str))
