"""
agcl.collab.presence — presence heartbeats, typing indicators, ambient config.

Presence is in-memory only (no persistence needed — it's ephemeral).
Heartbeats expire after 30s; typing indicators expire after 3s.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# space_id → {user_id: {"status": str, "expires_at": float}}
_presence: Dict[str, Dict[str, dict]] = {}


def _space_presence(space_id: str) -> Dict[str, dict]:
    return _presence.setdefault(space_id, {})


def set_presence(space_id: str, user_id: str, status: str, ttl: float) -> None:
    _space_presence(space_id)[user_id] = {
        "status": status,
        "expires_at": time.time() + ttl,
    }


def get_online(space_id: str) -> Dict[str, str]:
    """Return {user_id: status} for non-expired entries."""
    now = time.time()
    sp = _space_presence(space_id)
    return {uid: v["status"] for uid, v in sp.items() if v["expires_at"] > now}


def attach_presence_routes(r: APIRouter) -> None:
    """Attach presence routes to an existing APIRouter."""
    from agcl.collab.spaces import get_space
    from agcl.collab.router import broadcast

    class PresenceBody(BaseModel):
        user_id: str
        status: str = "online"

    class TypingBody(BaseModel):
        user_id: str

    @r.post("/spaces/{space_id}/presence")
    async def post_presence(space_id: str, body: PresenceBody):
        if not get_space(space_id):
            raise HTTPException(404, "space not found")
        set_presence(space_id, body.user_id, body.status, ttl=30.0)
        await broadcast(space_id, {
            "type": "presence",
            "data": {"user_id": body.user_id, "status": body.status},
        })
        return {"ok": True}

    @r.post("/spaces/{space_id}/typing")
    async def post_typing(space_id: str, body: TypingBody):
        if not get_space(space_id):
            raise HTTPException(404, "space not found")
        set_presence(space_id, body.user_id, "typing", ttl=3.0)
        await broadcast(space_id, {
            "type": "presence",
            "data": {"user_id": body.user_id, "status": "typing"},
        })
        # schedule offline event after TTL
        asyncio.create_task(_expire_typing(space_id, body.user_id))
        return {"ok": True}

    @r.get("/spaces/{space_id}/presence")
    def get_presence(space_id: str):
        if not get_space(space_id):
            raise HTTPException(404, "space not found")
        return {"presence": get_online(space_id)}


async def _expire_typing(space_id: str, user_id: str) -> None:
    from agcl.collab.router import broadcast
    await asyncio.sleep(3.1)
    sp = _space_presence(space_id)
    entry = sp.get(user_id)
    if entry and entry["status"] == "typing" and entry["expires_at"] <= time.time():
        sp.pop(user_id, None)
        await broadcast(space_id, {
            "type": "presence",
            "data": {"user_id": user_id, "status": "offline"},
        })
