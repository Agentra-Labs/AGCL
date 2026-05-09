"""
WebRTC node-transport helpers.

Goal: peer-to-peer GUI <-> AGCL node over a DataChannel, with NAT
traversal via STUN/TURN. The full peer connection lives in aiortc —
that's the long-running daemon. This module is the *signaling*
half: it exchanges SDP offers/answers and ICE candidates over an
HTTP signaling channel (the AGCL node itself or a relay Worker).

Why split it: a useful baseline is signaling-only (offer/answer
exchange + ICE collection), and you can wire that into a DataChannel
when you actually need it. Including the full aiortc loop here would
turn this into a daemon, which doesn't fit the toolkit model.

The HTTP-signaling pattern:

    GUI                         AGCL signaling endpoint
    ----                        -----------------------
    POST /webrtc/offer  ────►   stash sdp under {session_id}
    GET  /webrtc/answer ◄────   poll until peer answers
    POST /webrtc/ice    ────►   add candidate
    GET  /webrtc/ice    ◄────   pull peer candidates

This module exposes the in-memory store + the offer/answer signing
helpers; the FastAPI routes live in agcl.toolkit.endpoints.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def is_enabled() -> bool:
    return os.getenv("AGCL_WEBRTC_ENABLED", "0").lower() in ("1", "true", "yes", "on")


def stun_urls() -> List[str]:
    raw = os.getenv("AGCL_STUN_URL", "stun:stun.l.google.com:19302")
    return [u.strip() for u in raw.split(",") if u.strip()]


def turn_config() -> Optional[Dict[str, str]]:
    url = os.getenv("AGCL_TURN_URL", "").strip()
    if not url:
        return None
    return {
        "urls":       url,
        "username":   os.getenv("AGCL_TURN_USERNAME", ""),
        "credential": os.getenv("AGCL_TURN_PASSWORD", ""),
    }


def ice_servers() -> List[Dict[str, Any]]:
    """RTCConfiguration.iceServers payload — pass straight to RTCPeerConnection."""
    out: List[Dict[str, Any]] = [{"urls": stun_urls()}]
    turn = turn_config()
    if turn:
        out.append({k: v for k, v in turn.items() if v})
    return out


# ----------------------------------------------------------------------
# In-memory signaling store
# ----------------------------------------------------------------------

@dataclass
class _Session:
    offer:  Optional[Dict[str, Any]] = None
    answer: Optional[Dict[str, Any]] = None
    ice_a:  List[Dict[str, Any]] = field(default_factory=list)  # node-side candidates
    ice_b:  List[Dict[str, Any]] = field(default_factory=list)  # peer-side candidates
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)


_SESSIONS: Dict[str, _Session] = defaultdict(_Session)
_LOCK = asyncio.Lock()
TTL_SECONDS = 300


async def post_offer(session_id: str, sdp: Dict[str, Any]) -> None:
    async with _LOCK:
        s = _SESSIONS[session_id]
        s.offer = sdp
        s.last_active = time.time()


async def post_answer(session_id: str, sdp: Dict[str, Any]) -> None:
    async with _LOCK:
        s = _SESSIONS[session_id]
        s.answer = sdp
        s.last_active = time.time()


async def get_offer(session_id: str) -> Optional[Dict[str, Any]]:
    async with _LOCK:
        return _SESSIONS.get(session_id).offer if session_id in _SESSIONS else None


async def get_answer(session_id: str) -> Optional[Dict[str, Any]]:
    async with _LOCK:
        return _SESSIONS.get(session_id).answer if session_id in _SESSIONS else None


async def add_ice(session_id: str, side: str, candidate: Dict[str, Any]) -> None:
    if side not in ("a", "b"):
        raise ValueError("side must be 'a' or 'b'")
    async with _LOCK:
        s = _SESSIONS[session_id]
        (s.ice_a if side == "a" else s.ice_b).append(candidate)
        s.last_active = time.time()


async def pull_ice(session_id: str, side: str) -> List[Dict[str, Any]]:
    """Pop and return all collected candidates for one side."""
    if side not in ("a", "b"):
        raise ValueError("side must be 'a' or 'b'")
    async with _LOCK:
        s = _SESSIONS.get(session_id)
        if s is None:
            return []
        bucket = s.ice_a if side == "a" else s.ice_b
        out, bucket[:] = list(bucket), []
        s.last_active = time.time()
        return out


async def gc(now: Optional[float] = None) -> int:
    """Drop stale sessions. Returns count dropped."""
    now = now or time.time()
    dropped = 0
    async with _LOCK:
        for sid in list(_SESSIONS):
            if now - _SESSIONS[sid].last_active > TTL_SECONDS:
                del _SESSIONS[sid]
                dropped += 1
    return dropped


def status() -> Dict[str, Any]:
    return {
        "enabled":      is_enabled(),
        "ice_servers":  ice_servers(),
        "ttl_seconds":  TTL_SECONDS,
        "active_sessions": len(_SESSIONS),
    }
