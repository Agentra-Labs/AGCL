"""relay.broker — WebSocket connection registry and message fan-out."""
from __future__ import annotations

import json
from typing import Any, Dict, Set

_node_conns: Dict[str, Set[Any]] = {}
_client_conns: Dict[str, Set[Any]] = {}


def _nodes(space_id: str) -> Set[Any]:
    return _node_conns.setdefault(space_id, set())


def _clients(space_id: str) -> Set[Any]:
    return _client_conns.setdefault(space_id, set())


async def broadcast_to_space(space_id: str, event: dict, exclude=None) -> None:
    payload = json.dumps(event)
    for ws in list(_nodes(space_id)) | list(_clients(space_id)):
        if ws is exclude:
            continue
        try:
            await ws.send_text(payload)
        except Exception:
            pass


def register_node(space_id: str, ws: Any) -> None:
    _nodes(space_id).add(ws)


def unregister_node(space_id: str, ws: Any) -> None:
    _nodes(space_id).discard(ws)


def register_client(space_id: str, ws: Any) -> None:
    _clients(space_id).add(ws)


def unregister_client(space_id: str, ws: Any) -> None:
    _clients(space_id).discard(ws)
