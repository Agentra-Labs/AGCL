# Deploy an S3-compatible checkpoint store

AGCL trains a small projection MLP per topic. After the first
~1–3 minute training pass, the trained weights live forever in
`.agent_state/mas_topics/<topic_id>/` — about 1–4 MB per topic, but
they pile up. If you run a multi-node fleet (or you just want offsite
backup), point the checkpoint store at an S3-compatible bucket.

> Source: [agcl/toolkit/s3_store.py](../../agcl/toolkit/s3_store.py)
> · reference: [docs/integrations/cloud/s3.md](../integrations/cloud/s3.md)

---

## What you're building

```
   AGCL node A                                   AGCL node B
   ────────────                                   ────────────
        │   save_blob(topic_id, "links.pt", …)         │
        │   load_blob(topic_id, "meta.json")           │
        │                                              │
        └──────────────► ┌──────────────┐ ◄────────────┘
                         │  S3 bucket   │   ← single source of truth
                         │  (any        │      across all nodes
                         │   provider)  │
                         └──────────────┘
```

AGCL's `S3CheckpointStore` speaks the AWS SDK protocol, which means
it works with:

- **AWS S3** (primary)
- **Cloudflare R2** (S3-compatible, zero egress fees)
- **Backblaze B2** (S3-compatible, cheap storage)
- **MinIO** (self-hosted S3-compatible)
- **Wasabi**, **iDrive e2**, **Linode Object Storage**, etc.

## Prerequisites

| Need                                                | How                                             |
|-----------------------------------------------------|--------------------------------------------------|
| An S3-compatible bucket                             | depends on provider                              |
| Access key id + secret                              | provider's IAM / API key page                    |
| `aioboto3` Python package                            | `pip install aioboto3`                           |

---

## Step 1 — Pick a backend

### AWS S3

```bash
aws s3 mb s3://agcl-checkpoints --region us-east-1
# or via the console
```

For a service account dedicated to AGCL (recommended):

```bash
aws iam create-user --user-name agcl-node
aws iam attach-user-policy --user-name agcl-node \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess   # narrow this in prod
aws iam create-access-key --user-name agcl-node
# → returns AccessKeyId + SecretAccessKey
```

### Cloudflare R2 (no egress fees — recommended for cost)

1. Cloudflare dashboard → **R2** → Create bucket `agcl-checkpoints`.
2. R2 → **Manage R2 API Tokens** → Create with "Object Read & Write"
   scoped to that bucket.
3. Note the **Account ID** (URL: `https://<account-id>.r2.cloudflarestorage.com`).

### Backblaze B2 (cheapest storage)

1. b2.backblaze.com → Buckets → Create bucket `agcl-checkpoints`
   (Private).
2. App Keys → Create new application key with read/write to that
   bucket.
3. Endpoint URL: `https://s3.<region>.backblazeb2.com`.

### MinIO (self-hosted, free)

```bash
docker run -d --name minio \
    -p 9000:9000 -p 9001:9001 \
    -e MINIO_ROOT_USER=minioadmin \
    -e MINIO_ROOT_PASSWORD=$(openssl rand -hex 16) \
    -v $(pwd)/minio-data:/data \
    quay.io/minio/minio server /data --console-address ":9001"

# Then in the MinIO console at http://localhost:9001:
#   Buckets → Create bucket → 'agcl-checkpoints'
#   Service Accounts → New → save the key id + secret
```

---

## Step 2 — Tell AGCL about the bucket

Five env vars:

```bash
# Required
echo "AGCL_S3_BUCKET=agcl-checkpoints"       >> .env
echo "AGCL_S3_KEY_ID=AKIA…"                  >> .env
echo "AGCL_S3_SECRET=wJalr…"                 >> .env

# Required for non-AWS backends
echo "AGCL_S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com" >> .env

# Optional
echo "AGCL_S3_REGION=auto"          >> .env   # R2 wants 'auto'; AWS wants e.g. 'us-east-1'
echo "AGCL_S3_PREFIX=topics/"        >> .env   # namespace within the bucket
```

Backend cheat-sheet:

| Backend      | `AGCL_S3_ENDPOINT`                                       | `AGCL_S3_REGION` |
|--------------|----------------------------------------------------------|------------------|
| AWS S3        | omit                                                     | e.g. `us-east-1` |
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com`         | `auto`           |
| Backblaze B2  | `https://s3.<region>.backblazeb2.com`                    | `<region>`       |
| MinIO local   | `http://localhost:9000`                                  | `us-east-1`      |

Reload via Setup → Cloud providers → **Reload .env**, or restart.

---

## Step 3 — Smoke-test the connection

```bash
python -c "
import asyncio, os
os.environ.setdefault('AGCL_S3_BUCKET', 'agcl-checkpoints')
# (other env vars from .env are picked up automatically)
from agcl.toolkit.s3_store import S3CheckpointStore
async def main():
    s = S3CheckpointStore()
    print(await s.ping())
    print(await s.list_topics())
asyncio.run(main())
"
# → {'ok': True, 'kind': 's3', 'bucket': 'agcl-checkpoints', ...}
```

Or via the GUI: **Toolkit** tab → `s3` row → **Ping**.

CLI:

```bash
python main.py toolkit ping s3
```

---

## Step 4 — Verify checkpoint write through

Run a recursive MAS turn — it'll train a topic and persist:

```bash
curl -X POST http://localhost:9876/node/mas/run \
     -H "Authorization: Bearer $AGCL_NODE_AUTH" \
     -H "Content-Type: application/json" \
     -d '{"message": "What is photosynthesis?"}'
# → {"answer": "...", "topic_id": "abc123...", ...}
```

After it completes, check the bucket:

```bash
# AWS:
aws s3 ls s3://agcl-checkpoints/topics/

# R2:
wrangler r2 object list agcl-checkpoints --prefix topics/

# MinIO (mc CLI):
mc ls minio/agcl-checkpoints/topics/
```

You should see three files per topic: `meta.json`, `centroid.pt`,
`links.pt`.

Or check via the GUI: **Toolkit** tab → "Checkpoints (S3 or local FS)"
→ **Refresh**. The store kind should now read `s3` and your topics
should appear.

---

## What goes to S3 vs. stays local

| Data                                      | With S3 store                              | Without                          |
|-------------------------------------------|--------------------------------------------|-----------------------------------|
| Topic checkpoints (`mas_topics/<id>/`)    | bucket (key = `<prefix>/<topic_id>/<name>`) | `<STATE_DIR>/mas_topics/<id>/`    |
| Chat sessions                             | local FS (use Redis — see [redis_state.md](redis_state.md)) | local FS |
| Usage / quotas / custom providers          | local FS                                    | local FS                          |
| HF model snapshots                         | local FS (large; don't put in S3)           | local FS                          |
| Downloaded GGUF files                      | local FS                                    | local FS                          |

S3 only holds the small per-topic checkpoints. The rest stays on the
node.

---

## Cost notes

For a typical AGCL deployment (50 topics, ~3 MB each = 150 MB):

| Provider       | Monthly cost                             |
|----------------|-------------------------------------------|
| Cloudflare R2  | ~$0.002 (storage) + $0 egress             |
| AWS S3 Standard| ~$0.003 + $0.09/GB out                    |
| Backblaze B2   | ~$0.001 + $0.01/GB out                    |
| MinIO local    | $0 + your own disk                        |

**Egress is the dominant cost** if your AGCL nodes are outside the
provider's network. R2's zero-egress is a big deal for multi-region
fleets.

---

## Lifecycle / cleanup

Topics never expire automatically — delete them when the conversation
they back is no longer needed:

```bash
# From the GUI: Topics tab → Delete
# Or via API:
curl -X DELETE http://localhost:9876/node/topics/<topic_id> \
     -H "Authorization: Bearer $AGCL_NODE_AUTH"
```

For automatic expiry, set a bucket lifecycle rule:

```bash
# AWS — delete objects older than 90 days
aws s3api put-bucket-lifecycle-configuration --bucket agcl-checkpoints \
    --lifecycle-configuration '{"Rules":[{"ID":"expire","Status":"Enabled","Filter":{"Prefix":"topics/"},"Expiration":{"Days":90}}]}'
```

R2 and B2 have similar lifecycle UIs.

---

## Common problems

| Symptom                                          | Fix                                                                                            |
|--------------------------------------------------|------------------------------------------------------------------------------------------------|
| `aioboto3` not installed                         | `pip install aioboto3`                                                                          |
| `botocore.exceptions.ClientError: NoSuchBucket`  | Bucket name wrong, or you forgot to create it.                                                   |
| `SignatureDoesNotMatch`                          | `AGCL_S3_REGION` doesn't match the bucket's actual region. R2 → `auto`; AWS → exact region.    |
| `EndpointConnectionError`                        | `AGCL_S3_ENDPOINT` typo, or the endpoint hostname doesn't resolve from your network.            |
| 403 on every write                                | IAM permissions too narrow. Grant at least `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`. |
| Old topics still showing local paths             | The node was started before `AGCL_S3_BUCKET` was set — restart.                                  |
| Slow checkpoint saves                            | S3 round-trip per write. Acceptable when training (~1ms per file × 3 files). If it's hot path latency, batch them. |

---

## Operating notes

- **Encryption**: enable bucket-level encryption (SSE-S3 or SSE-KMS).
  AGCL doesn't encrypt blobs client-side — it relies on the bucket.
- **Cross-region replication**: enable on the bucket if you want
  multi-region resilience. AGCL won't know — reads come from whichever
  region you point at.
- **Quotas**: not currently AGCL-tracked. Use S3 inventory + a cron
  job to alert on bucket size growth.
- **Migration**: to move from local-FS to S3 (or vice versa), copy
  the `STATE_DIR/topics/` tree out, set/unset `AGCL_S3_BUCKET`,
  restart. The store auto-discovers what's there.

---

## What to read next

- [redis_state.md](redis_state.md) — the natural pair for distributed
  session state.
- [k8s.md](k8s.md) — typical multi-replica setup that uses both.
- Reference: [docs/integrations/cloud/s3.md](../integrations/cloud/s3.md).
