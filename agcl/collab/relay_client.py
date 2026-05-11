"""
agcl.collab.relay_client — optional bridge to the cloud relay service.

Activated when AGCL_RELAY_URL env var is set. On startup, connects to
the relay WebSocket and forwards collab events bidirectionally.

Usage (automatic — called from node startup):
    from agcl.collab.relay_client import start_relay_bridge
    asyncio.create_task(start_relay_bridge(node_id, space_ids))
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import List, Optional


RELAY_URL = os.environ.get("AGCL_RELAY_URL", "")


async def start_relay_bridge(node_id: str, space_ids: Optional[List[str]] = None) -> None:
    """Connect to relay and bridge collab events. Runs forever; reconnects on drop."""
    if not RELAY_URL:
        return
    while True:
        try:
            await _run_bridge(node_id, space_ids or [])
        except Exception as e:
            print(f"[relay] bridge error: {e}; reconnecting in 5s")
            await asyncio.sleep(5)


async def _run_bridge(node_id: str, space_ids: List[str]) -> None:
    try:
        import websockets  # type: ignore
    except ImportError:
        print("[relay] websockets not installed; relay bridge disabled")
        return

    url = f"{RELAY_URL.rstrip('/')}/relay/node/{node_id}"
    async with websockets.connect(url) as ws:
        print(f"[relay] connected to {url}")
        # register spaces
        await ws.send(json.dumps({"type": "register", "space_ids": space_ids}))

        async def _recv():
            async for raw in ws:
                try:
                    event = json.loads(raw)
                    space_id = event.get("space_id")
                    if space_id:
                        from agcl.collab.router import broadcast
                        await broadcast(space_id, event)
                except Exception:
                    pass

        await _recv()
