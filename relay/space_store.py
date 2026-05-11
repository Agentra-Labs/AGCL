"""relay.space_store — SQLite-backed space state for nodeless hosting."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path("relay_state.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS spaces (
            id TEXT PRIMARY KEY,
            name TEXT,
            invite_code TEXT UNIQUE,
            node_url TEXT,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            space_id TEXT,
            payload TEXT,
            created_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_space ON messages(space_id, created_at);
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            relay_token TEXT,
            space_ids TEXT,
            registered_at REAL
        );
        """)


def create_space(name: str, node_url: str = "") -> Dict[str, Any]:
    space_id = secrets.token_urlsafe(8)
    invite_code = secrets.token_urlsafe(6)
    with _conn() as c:
        c.execute(
            "INSERT INTO spaces VALUES (?,?,?,?,?)",
            (space_id, name, invite_code, node_url, time.time()),
        )
    return {"space_id": space_id, "invite_code": invite_code, "node_url": node_url}


def resolve_invite(code: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute("SELECT * FROM spaces WHERE invite_code=?", (code,)).fetchone()
    if not row:
        return None
    return dict(row)


def buffer_message(space_id: str, payload: dict) -> None:
    msg_id = payload.get("id") or secrets.token_urlsafe(8)
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO messages VALUES (?,?,?,?)",
            (msg_id, space_id, json.dumps(payload), time.time()),
        )
        # keep only last 500 per space
        c.execute("""
            DELETE FROM messages WHERE space_id=? AND id NOT IN (
                SELECT id FROM messages WHERE space_id=? ORDER BY created_at DESC LIMIT 500
            )
        """, (space_id, space_id))


def get_buffered(space_id: str, limit: int = 500) -> List[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT payload FROM messages WHERE space_id=? ORDER BY created_at ASC LIMIT ?",
            (space_id, limit),
        ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def register_node(node_id: str, space_ids: List[str]) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO nodes VALUES (?,?,?,?)",
            (node_id, token, json.dumps(space_ids), time.time()),
        )
    return token
