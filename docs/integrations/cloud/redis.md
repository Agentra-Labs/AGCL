# Redis / Valkey — distributed session state

<p align="left">
  <img src="../../assets/redis.svg" width="22" alt="Redis" />&nbsp;
  <a href="https://img.shields.io/badge/Valkey-7.x-008B6B"><img alt="Valkey" src="https://img.shields.io/badge/Valkey-7.x-008B6B"></a>
  <a href="https://img.shields.io/badge/protocol-RESP3-DC382D"><img alt="RESP3" src="https://img.shields.io/badge/protocol-RESP3-DC382D"></a>
</p>

**What it does:** Replaces local `.agent_state` file with a fast
networked key-value store. Enables cluster mode, multiple GUI clients,
remote nodes, horizontal scaling of AGCL workers.

**Valkey** is the community-maintained Redis fork (Redis changed
license in 2024); use either — API is identical.

> Part of the **[Cloud / infra integrations](../cloud.md)**.

---

## Install

```bash
# Docker (local dev)
docker run -d -p 6379:6379 --name agcl-redis redis:7-alpine

# Or Valkey
docker run -d -p 6379:6379 --name agcl-redis valkey/valkey:latest

# With persistence
docker run -d -p 6379:6379 \
  -v $(pwd)/redis-data:/data \
  redis:7-alpine redis-server --appendonly yes

pip install 'redis[hiredis]'     # Python client
```

---

## AGCL integration pattern

```python
import json
import redis.asyncio as aioredis

class DistributedAgentState:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.r = aioredis.from_url(redis_url, decode_responses=True)

    async def save_state(self, agent_id: str, state: dict):
        key = f"agcl:state:{agent_id}"
        await self.r.set(key, json.dumps(state))
        await self.r.expire(key, 86400)   # 24h TTL

    async def load_state(self, agent_id: str) -> dict | None:
        raw = await self.r.get(f"agcl:state:{agent_id}")
        return json.loads(raw) if raw else None

    async def publish_event(self, channel: str, event: dict):
        await self.r.publish(f"agcl:events:{channel}", json.dumps(event))

    async def track_pressure(self, node_id: str, pressure: float):
        await self.r.zadd("agcl:pressure", {node_id: pressure})

    async def get_node_ranking(self) -> list:
        return await self.r.zrange("agcl:pressure", 0, -1, withscores=True)
```

---

## Key schema for AGCL

```
agcl:state:<agent_id>          → JSON blob of agent state
agcl:session:<session_id>      → conversation history
agcl:pressure                  → sorted set: node_id → pressure score
agcl:topic:<topic_id>          → topic centroid metadata
agcl:events:<channel>          → pub/sub channel for node coordination
agcl:gui:<client_id>           → active GUI sessions
```

---

## Env vars

```env
AGCL_REDIS_URL=redis://localhost:6379

# With auth:
AGCL_REDIS_URL=redis://:yourpassword@redis-host:6379

# TLS (Redis Cloud, Upstash, etc.):
AGCL_REDIS_URL=rediss://default:token@my-cluster.upstash.io:6380
```

---

## Managed Redis options

| Provider | Notes |
|---|---|
| **Upstash** | Serverless Redis, generous free tier, HTTP REST API also available |
| **Redis Cloud** | Managed Redis by Redis Ltd, TLS by default |
| **Fly.io Redis** | Run Valkey on Fly volume — very cheap |
| **self-hosted** | `docker run -d valkey/valkey` on any VPS |
