"""
Cloudflare Workers + Durable Objects edge-relay client.

The Worker (deployed separately, see docs/integrations/cloud/cloudflare.md)
exposes:
    POST /publish?session=<id>     accept token from AGCL node
    GET  /subscribe?session=<id>   SSE feed for GUI clients

This module is the AGCL-node-side client: pushes deltas/events to the
relay so multiple GUI clients can subscribe to the same session through
a public, NAT-traversable URL.

If AGCL_RELAY_URL isn't set, every method is a no-op so AGCL keeps
working when the relay isn't deployed.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator, Dict, Optional

import httpx


def relay_url() -> Optional[str]:
    url = os.getenv("AGCL_RELAY_URL", "").strip().rstrip("/")
    return url or None


def is_enabled() -> bool:
    return relay_url() is not None


async def publish(session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Push one event to the Worker. Silent no-op if relay isn't set."""
    base = relay_url()
    if not base:
        return {"ok": False, "skipped": True, "reason": "AGCL_RELAY_URL unset"}
    async with httpx.AsyncClient(timeout=10) as h:
        r = await h.post(
            f"{base}/publish",
            params={"session": session_id},
            json=event,
            headers=_auth_headers(),
        )
        return {"ok": r.status_code < 400, "status": r.status_code}


async def publish_token(session_id: str, token: str) -> Dict[str, Any]:
    """Convenience: publish a single token delta as `{"token": "..."}`."""
    return await publish(session_id, {"token": token})


async def subscribe(session_id: str) -> AsyncIterator[Dict[str, Any]]:
    """
    SSE consumer for the relay. Yields parsed JSON events. Useful when
    AGCL itself is the consumer (e.g. multi-node coordination).
    """
    base = relay_url()
    if not base:
        return
    async with httpx.AsyncClient(timeout=None) as h:
        async with h.stream(
            "GET", f"{base}/subscribe",
            params={"session": session_id},
            headers={"Accept": "text/event-stream", **_auth_headers()},
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    yield {"raw": payload}


async def ping() -> Dict[str, Any]:
    base = relay_url()
    if not base:
        return {"ok": False, "reason": "AGCL_RELAY_URL unset"}
    try:
        async with httpx.AsyncClient(timeout=5) as h:
            # A HEAD on the worker root is the cheapest reachability check.
            r = await h.head(base, headers=_auth_headers())
        return {"ok": r.status_code < 500, "status": r.status_code, "url": base}
    except httpx.HTTPError as e:
        return {"ok": False, "url": base, "error": str(e)}


def _auth_headers() -> Dict[str, str]:
    token = os.getenv("AGCL_RELAY_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}
