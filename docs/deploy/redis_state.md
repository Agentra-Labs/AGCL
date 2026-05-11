# Deploy Redis / Valkey for distributed AGCL state

If you run **more than one AGCL node** (load-balanced replicas, a
fleet of nodes co-ordinating), you need shared state — otherwise pod
A doesn't see pod B's sessions. Redis (or its open-source fork
Valkey) is AGCL's distributed-state backend.

> Source: [agcl/toolkit/redis_state.py](../../agcl/toolkit/redis_state.py)
> · reference: [docs/integrations/cloud/redis.md](../integrations/cloud/redis.md)

For a single-node deployment, **skip this** — AGCL's local FS store
is fine.

---

## What you're building

```
   AGCL node 1                                   AGCL node 2
   ────────────                                   ────────────
        │   GET/SET agcl:session:<id>                  │
        │   GET/SET agcl:topic:<id>                    │
        │   ZADD    agcl:pressure                       │
        │                                              │
        └──────────────► ┌──────────────┐ ◄────────────┘
                         │  Redis /     │
                         │  Valkey       │   ← single source of truth
                         │   ─ port 6379 │
                         └──────────────┘
```

AGCL's `redis_state.py` defines the key schema:

| Key                          | Type        | What it stores                          |
|------------------------------|-------------|------------------------------------------|
| `agcl:state:<agent_id>`      | string      | JSON blob of agent state                |
| `agcl:session:<session_id>`  | string      | Conversation history (JSON)             |
| `agcl:topic:<topic_id>`      | string      | Topic centroid metadata                  |
| `agcl:pressure`              | sorted set  | `node_id → pressure score`              |
| `agcl:events:<channel>`      | pub/sub     | Inter-node coordination messages         |

## Prerequisites

| Need                                            | How                                      |
|-------------------------------------------------|------------------------------------------|
| A Redis-compatible server ≥ 7.0                | `docker pull redis` or any managed service|
| The `redis` Python package                      | `pip install 'redis[hiredis]'`           |
| Multiple AGCL nodes if you want the benefit     | n/a                                       |

---

## Step 1 — Run Redis

### Option A — Docker (single instance, fine for dev)

```bash
docker run -d --name redis \
    -p 6379:6379 \
    -v $(pwd)/redis-data:/data \
    --restart unless-stopped \
    redis:7-alpine \
    redis-server --appendonly yes
```

With persistence on (`--appendonly yes`), data survives container restarts.

### Option B — Docker Compose (with password)

```yaml
# docker-compose.redis.yml
services:
  redis:
    image: redis:7-alpine
    command:
      - redis-server
      - --appendonly
      - "yes"
      - --requirepass
      - "${REDIS_PASSWORD}"
    ports: ["6379:6379"]
    volumes: ["./redis-data:/data"]
    restart: unless-stopped
```

```bash
REDIS_PASSWORD=$(openssl rand -hex 24) docker compose -f docker-compose.redis.yml up -d
```

### Option C — Managed (cloud)

- **AWS ElastiCache** — Redis OSS or Valkey; cheapest is `cache.t3.micro`.
- **GCP Memorystore** — managed Redis.
- **Upstash** — serverless, pay-per-request, has a free tier.
- **Redis Cloud** — hosted by the Redis company.

For dev / personal, **Upstash free tier** (10k commands/day) is
plenty for a multi-node setup.

### Option D — Valkey (open-source Redis fork)

Drop-in replacement, no license worries:

```bash
docker run -d --name valkey -p 6379:6379 valkey/valkey:7
```

`redis_state.py` works identically — the wire protocol is the same.

---

## Step 2 — Smoke-test the connection

```bash
docker exec -it redis redis-cli ping
# → PONG

# With password
docker exec -it redis redis-cli -a "$REDIS_PASSWORD" ping
```

Or from Python:

```bash
python -c "
import os
os.environ['AGCL_REDIS_URL'] = 'redis://localhost:6379'
import asyncio
from agcl.toolkit.redis_state import RedisStateStore
async def main():
    s = RedisStateStore()
    await s.connect()
    print(await s.ping())
asyncio.run(main())
"
# → {'ok': True, 'kind': 'redis', 'url': 'redis://localhost:6379', ...}
```

---

## Step 3 — Wire AGCL to Redis

```bash
echo "AGCL_REDIS_URL=redis://localhost:6379"               >> .env
# With password:
echo "AGCL_REDIS_URL=redis://:${REDIS_PASSWORD}@host:6379" >> .env
# Upstash:
echo "AGCL_REDIS_URL=rediss://default:TOKEN@us1-eager-piranha-12345.upstash.io:6379" >> .env
```

Reload via Setup → Cloud providers → **Reload .env**, or restart the
node.

The `agcl.toolkit.registry.state_store()` factory checks
`AGCL_REDIS_URL` and returns a `RedisStateStore` if set; otherwise it
falls back to the local-FS `LocalStateStore`. No code change needed.

---

## Step 4 — Verify multi-node coherence

Start two AGCL nodes pointing at the same Redis:

```bash
# Terminal 1
AGCL_NODE_AUTH=$(openssl rand -hex 32) python main.py node --port 9876 --cors "*"
# copy the key as KEY1

# Terminal 2
AGCL_NODE_AUTH=$(openssl rand -hex 32) python main.py node --port 9877 --cors "*"
# copy the key as KEY2
```

Now:

```bash
# Create a session on node 1
curl -X POST http://localhost:9876/node/chat/test1 \
     -H "Authorization: Bearer $KEY1" \
     -H "Content-Type: application/json" \
     -d '{"message": "remember the number 42"}'

# Read it from node 2
curl http://localhost:9877/node/sessions/test1 \
     -H "Authorization: Bearer $KEY2"
# → {messages: [...]}   # the message you just sent shows up here
```

If node 2 sees the session, Redis is wired correctly.

---

## Step 5 — Toolkit ping

GUI: **Toolkit** tab → `redis` row → **Ping**. Should toast
`{ok: true, kind: "redis", ...}`.

CLI:

```bash
python main.py toolkit ping redis
```

---

## Kubernetes deployment

Use the Bitnami Redis Helm chart (no auth in the example — add it
for production):

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install agcl-redis bitnami/redis \
    --namespace agcl \
    --set auth.enabled=false \
    --set master.persistence.size=8Gi \
    --set replica.replicaCount=0
```

Then in your AGCL `agcl-env` secret:

```bash
kubectl create secret generic agcl-env \
    --from-env-file=.env \
    --from-literal=AGCL_REDIS_URL=redis://agcl-redis-master:6379 \
    --dry-run=client -o yaml | kubectl replace -f -

kubectl rollout restart deploy/agcl-node
```

For HA: bump `replica.replicaCount` and enable sentinel (see
[docs/integrations/cloud/redis.md](../integrations/cloud/redis.md)).

---

## Persistence + backup

Redis persists in two modes:

- **AOF** (append-only file) — every write is logged. Slower, almost
  no data loss. `--appendonly yes`.
- **RDB** snapshots — periodic dumps. Faster, last few seconds may be
  lost. Default.

For AGCL, AOF is recommended — losing sessions on a restart is
annoying. Both can be enabled together.

Back up manually:

```bash
docker exec redis redis-cli SAVE
docker cp redis:/data/dump.rdb ./redis-backup-$(date +%F).rdb
```

For Upstash / ElastiCache / Memorystore, backups are automatic — see
their docs.

---

## What ends up in Redis vs. locally

| Data                              | Where with Redis enabled            | Where without                        |
|-----------------------------------|--------------------------------------|---------------------------------------|
| Chat sessions (`sess_*.json`)     | `agcl:session:<id>`                  | `<STATE_DIR>/sess_*.json`             |
| MAS topics (`mas_topics/<id>/`)   | local FS (use S3 store for distributed — see [s3_store.md](s3_store.md)) | local FS |
| Usage / quota records             | local FS (not yet Redis-backed)      | local FS                              |
| Custom-provider registry          | local FS                              | local FS                              |
| Pressure scores                   | `agcl:pressure` (ZSET)               | per-process only                      |

So for true horizontal scaling you typically want **Redis + S3**: the
session/state goes to Redis, the (much larger) topic checkpoints go
to S3 — see [s3_store.md](s3_store.md).

---

## Common problems

| Symptom                                       | Fix                                                                                          |
|-----------------------------------------------|----------------------------------------------------------------------------------------------|
| `MODULE redis is not installed`               | `pip install 'redis[hiredis]'`                                                                 |
| `AUTH failed` from Redis                      | URL is missing the password. Format: `redis://:PASSWORD@host:6379` (note the leading colon).   |
| Sessions still don't sync between nodes       | One node has `AGCL_REDIS_URL` set, the other doesn't. Run `python main.py toolkit ping redis` on each. |
| Latency added to every request                | Use a Redis instance close to the AGCL nodes (same region). Avoid cross-region calls.          |
| OOM-killed Redis                              | Set `maxmemory` + `maxmemory-policy allkeys-lru` so old keys are evicted.                       |
| TLS error to Upstash                          | Upstash needs `rediss://` (note the second 's'), not `redis://`.                               |

---

## Operating notes

- **Single AGCL node** doesn't benefit — local FS is faster.
- **Privacy**: Redis holds raw session content. Treat it like a
  database backup with secrets; encrypt the connection (`rediss://`)
  on any non-localhost link.
- **Key rotation**: changing `AGCL_REDIS_URL` requires a node restart
  (the store is built at startup; `Reload .env` doesn't reconnect).

---

## What to read next

- [s3_store.md](s3_store.md) — the natural pair for distributed
  topic checkpoints.
- [k8s.md](k8s.md) — the typical setup that pulls Redis in.
- Reference: [docs/integrations/cloud/redis.md](../integrations/cloud/redis.md).
