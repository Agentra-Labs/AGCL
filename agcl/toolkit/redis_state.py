"""
Distributed (Redis/Valkey) and local-file state stores.

Both implement the same StateStore protocol so AGCL code can be backed
by either without conditionals. Falls back to LocalStateStore when
AGCL_REDIS_URL isn't set so the toolkit imports cleanly even on a
single-host install.

Redis dep is optional — `pip install redis[hiredis]` enables the
RedisStateStore. If `redis` isn't importable, instantiating the class
raises ImportError with a hint.

Key schema (from docs/integrations/cloud/redis.md):
    agcl:state:<agent_id>      JSON blob of agent state
    agcl:session:<session_id>  conversation history
    agcl:topic:<topic_id>      topic centroid metadata
    agcl:pressure              sorted set: node_id -> pressure score
    agcl:events:<channel>      pub/sub channel for node coordination
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional


# ----------------------------------------------------------------------
# Local-file fallback (no Redis required)
# ----------------------------------------------------------------------

class LocalStateStore:
    """
    Single-process JSON-file store. Same surface as RedisStateStore so
    swapping is a one-line config change. Persists under
    `<state_dir>/toolkit/`.
    """

    def __init__(self, state_dir: Optional[str] = None):
        from agcl import config as cfg
        root = Path(state_dir or cfg.STATE_DIR) / "toolkit"
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self._lock = threading.Lock()
        self._pressure: Dict[str, float] = {}

    @property
    def kind(self) -> str: return "local"

    def _path(self, kind: str, key: str) -> Path:
        # url-safe key fragment
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.root / f"{kind}_{safe}.json"

    async def save_state(self, agent_id: str, state: Dict[str, Any], ttl: int = 86400) -> None:
        with self._lock:
            self._path("state", agent_id).write_text(json.dumps({
                "data": state, "expires_at": time.time() + ttl,
            }))

    async def load_state(self, agent_id: str) -> Optional[Dict[str, Any]]:
        p = self._path("state", agent_id)
        if not p.exists():
            return None
        with self._lock:
            obj = json.loads(p.read_text())
        if obj.get("expires_at", 0) < time.time():
            try: p.unlink()
            except FileNotFoundError: pass
            return None
        return obj["data"]

    async def delete_state(self, agent_id: str) -> bool:
        try:
            self._path("state", agent_id).unlink()
            return True
        except FileNotFoundError:
            return False

    async def track_pressure(self, node_id: str, score: float) -> None:
        with self._lock:
            self._pressure[node_id] = score

    async def node_ranking(self) -> List[tuple]:
        with self._lock:
            return sorted(self._pressure.items(), key=lambda kv: kv[1])

    async def publish_event(self, channel: str, event: Dict[str, Any]) -> None:
        # Local store has no fan-out; append-only event log per channel.
        p = self._path("events", channel)
        with self._lock:
            with open(p, "a") as f:
                f.write(json.dumps(event) + "\n")

    async def ping(self) -> Dict[str, Any]:
        return {"ok": True, "kind": "local", "path": str(self.root)}

    async def aclose(self) -> None:
        pass


# ----------------------------------------------------------------------
# Redis / Valkey (real)
# ----------------------------------------------------------------------

class RedisStateStore:
    """
    Async Redis-backed store. Uses redis.asyncio under the hood.

    `pip install 'redis[hiredis]'` is required.
    """

    def __init__(self, url: Optional[str] = None):
        try:
            import redis.asyncio as aioredis  # type: ignore
        except ImportError as e:
            raise ImportError(
                "RedisStateStore requires `pip install 'redis[hiredis]'`"
            ) from e
        self.url = url or os.environ["AGCL_REDIS_URL"]
        self.r = aioredis.from_url(self.url, decode_responses=True)

    @property
    def kind(self) -> str: return "redis"

    async def save_state(self, agent_id: str, state: Dict[str, Any], ttl: int = 86400) -> None:
        key = f"agcl:state:{agent_id}"
        await self.r.set(key, json.dumps(state))
        if ttl > 0:
            await self.r.expire(key, ttl)

    async def load_state(self, agent_id: str) -> Optional[Dict[str, Any]]:
        raw = await self.r.get(f"agcl:state:{agent_id}")
        return json.loads(raw) if raw else None

    async def delete_state(self, agent_id: str) -> bool:
        return bool(await self.r.delete(f"agcl:state:{agent_id}"))

    async def track_pressure(self, node_id: str, score: float) -> None:
        await self.r.zadd("agcl:pressure", {node_id: score})

    async def node_ranking(self) -> List[tuple]:
        return await self.r.zrange("agcl:pressure", 0, -1, withscores=True)

    async def publish_event(self, channel: str, event: Dict[str, Any]) -> None:
        await self.r.publish(f"agcl:events:{channel}", json.dumps(event))

    async def subscribe_events(self, channel: str) -> AsyncIterator[Dict[str, Any]]:
        """Async iterator of events arriving on the channel."""
        ps = self.r.pubsub()
        await ps.subscribe(f"agcl:events:{channel}")
        try:
            async for msg in ps.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                try:
                    yield json.loads(data) if isinstance(data, str) else data
                except (TypeError, json.JSONDecodeError):
                    continue
        finally:
            await ps.unsubscribe(f"agcl:events:{channel}")

    async def ping(self) -> Dict[str, Any]:
        try:
            ok = await self.r.ping()
            return {"ok": bool(ok), "kind": "redis", "url": self._safe_url()}
        except Exception as e:
            return {"ok": False, "kind": "redis", "url": self._safe_url(),
                    "error": str(e)}

    def _safe_url(self) -> str:
        # Strip password for display.
        u = self.url
        if "@" in u and "://" in u:
            scheme, rest = u.split("://", 1)
            authority, _, hostpart = rest.partition("@")
            if authority:
                return f"{scheme}://***@{hostpart}"
        return u

    async def aclose(self) -> None:
        await self.r.aclose()
