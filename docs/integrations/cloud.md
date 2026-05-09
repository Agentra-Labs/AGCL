# Cloud / infra integrations

Pieces of the wider AGCL deployment story: provider abstraction,
inference backends, distributed state, object storage, edge relays,
and node transports. Each priority is one page.

> Part of the **[Agent-platform integrations](../integrations.md)**.

---

## Priority order

| # | Topic | What it gives you |
|---|---|---|
| 1 | [LiteLLM](cloud/litellm.md) — provider abstraction gateway | One API for 100+ LLM providers; retry / fallback / spend tracking |
| 2 | [Ollama](cloud/ollama.md) — local / LAN / remote inference | Air-gapped capable; OpenAI-compatible at `:11434/v1` |
| 3 | [vLLM](cloud/vllm.md) — high-throughput inference backend | 10–50× Ollama under load; AGCL-as-a-service |
| 4 | [Redis / Valkey](cloud/redis.md) — distributed session state | Multi-GUI / multi-node / horizontal scale |
| 5 | [Kubernetes](cloud/k8s.md) — infra packaging | EKS / GKE / AKS / k3s / RunPod / Lambda |
| 6 | [S3-compatible storage](cloud/s3.md) — checkpoint persistence | Trained topics shared across nodes |
| 7 | [Cloudflare Workers + Durable Objects](cloud/cloudflare.md) — edge relay | NAT-busting GUI ↔ node, SSE fanout |
| 8 | [HuggingFace TGI](cloud/tgi.md) — legacy / HF Endpoints | Existing HF deployments; vLLM is the successor |
| 9 | [WebRTC node transport](cloud/webrtc.md) — peer-to-peer | Sub-100ms direct GUI ↔ node, NAT traversal |
| 10 | [GCP Cloud Run](cloud/gcp.md) — Cloud Run + Cloud Build + Secret Manager | Hosts the AGCL node and the MCP server with scale-to-zero |

For Discord (also a "cloud" surface) see
[../discord.md](discord.md). For the standalone Docker / Compose
quickstart see [../docker.md](docker.md). For headless invocation
(Cloud Run jobs, GitHub Actions, etc.), see
[../headless-run.md](headless-run.md).

---

## Stack diagram

```
┌─────────────────────────────────────────────────────┐
│                   AGCL GUI / Client                 │
│         (browser / Electron / Discord / CLI)        │
└────────────────┬────────────────────────────────────┘
                 │  HTTP / SSE / WebRTC / Discord WS
┌────────────────▼────────────────────────────────────┐
│           Cloudflare Edge Relay (optional)          │
│      Workers + Durable Objects — SSE fanout         │
└────────────────┬────────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────────┐
│              AGCL Core Node (K8s Pod / Docker)      │
│                                                     │
│  ┌─────────────┐    ┌────────────┐    ┌──────────┐  │
│  │  LiteLLM    │    │   Redis    │    │  S3 /    │  │
│  │  Gateway    │    │  (state /  │    │  MinIO   │  │
│  │             │    │  sessions) │    │ (topics) │  │
│  └──────┬──────┘    └────────────┘    └──────────┘  │
│         │                                           │
└─────────┼───────────────────────────────────────────┘
          │
    ┌─────▼─────────────────────────────┐
    │         Inference Backends         │
    │  Ollama  │  vLLM  │  TGI  │  API   │
    │ (local)  │(server)│ (HF)  │(cloud) │
    └────────────────────────────────────┘
```

---

## Master env-var reference

```env
# ── LiteLLM Gateway ──────────────────────────────────────────
AGCL_LLM_BASE_URL=http://localhost:4000
AGCL_LLM_API_KEY=sk-agcl-internal
AGCL_LLM_MODEL=smart                         # alias in litellm_config.yaml

# ── Ollama ───────────────────────────────────────────────────
OLLAMA_HOST=http://192.168.1.50:11434         # local | LAN | RunPod proxy

# ── vLLM ─────────────────────────────────────────────────────
VLLM_HOST=http://vllm-host:8000

# ── Redis / Valkey ───────────────────────────────────────────
AGCL_REDIS_URL=redis://localhost:6379

# ── S3-compatible storage ────────────────────────────────────
AGCL_S3_BUCKET=agcl-checkpoints
AGCL_S3_KEY_ID=your-key-id
AGCL_S3_SECRET=your-secret
AGCL_S3_ENDPOINT=                            # blank = AWS; set for R2/MinIO/B2

# ── Cloudflare Edge Relay ─────────────────────────────────────
AGCL_RELAY_URL=https://agcl-edge-relay.your-subdomain.workers.dev

# ── Discord ──────────────────────────────────────────────────
DISCORD_BOT_TOKEN=Bot.MTk4NjIy...
DISCORD_GUILD_ID=123456789012345678
DISCORD_AGENT_CHANNEL=987654321098765432

# ── WebRTC (future) ──────────────────────────────────────────
AGCL_WEBRTC_ENABLED=false
AGCL_STUN_URL=stun:stun.l.google.com:19302
```
