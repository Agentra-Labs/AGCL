# Deploy LiteLLM as AGCL's gateway

Put a LiteLLM proxy in front of AGCL so the node has **one** endpoint
that fans out to OpenAI, Anthropic, Together, Fireworks, Bedrock,
Azure OpenAI, vLLM, Ollama, and 100+ others — with automatic fallback,
retry, virtual keys, and spend tracking.

> Source: [agcl/toolkit/litellm_gw.py](../../agcl/toolkit/litellm_gw.py)
> · reference: [docs/integrations/cloud/litellm.md](../integrations/cloud/litellm.md)

---

## What you're building

```
                  AGCL node
                  ─────────
                       │  POST /v1/chat/completions
                       ▼
              ┌──────────────────┐
              │  LiteLLM proxy   │  (your container / service)
              │  (port 4000)     │
              └────┬─────┬─────┬─┘
                   │     │     │
       ┌───────────┘     │     └─────────────┐
       ▼                 ▼                   ▼
   OpenAI            Anthropic           Together / vLLM / …
```

AGCL talks **only** to LiteLLM. LiteLLM does the routing, fallback,
key rotation, and spend tracking. From AGCL's perspective there's
"one model called `smart`" — LiteLLM decides which provider answers.

## Prerequisites

| Need                                          | How                                     |
|-----------------------------------------------|------------------------------------------|
| Docker (or pip)                               | [docker.md](docker.md)                   |
| At least one provider API key                  | OpenAI / Anthropic / etc.               |
| (Optional) A Postgres DB for spend tracking    | any Postgres ≥ 12                       |

---

## Step 1 — Pick a deployment mode

### Mode A — single-container quickstart

Easiest. No DB, no UI, no virtual keys — just routing + fallback.

```bash
# Pull and run with a config volume
docker run -d --name litellm \
    -p 4000:4000 \
    -v $(pwd)/litellm-config.yaml:/app/config.yaml \
    -e OPENAI_API_KEY=$OPENAI_API_KEY \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    ghcr.io/berriai/litellm:main-latest \
    --config /app/config.yaml --port 4000
```

### Mode B — with database (production)

Adds: virtual keys, per-key budgets, spend reports, admin UI.

```bash
docker run -d --name litellm-db -p 5432:5432 \
    -e POSTGRES_PASSWORD=litellmpass \
    -e POSTGRES_DB=litellm \
    postgres:16

docker run -d --name litellm \
    -p 4000:4000 \
    -v $(pwd)/litellm-config.yaml:/app/config.yaml \
    -e OPENAI_API_KEY=$OPENAI_API_KEY \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    -e DATABASE_URL=postgresql://postgres:litellmpass@host.docker.internal:5432/litellm \
    -e LITELLM_MASTER_KEY=sk-master-$(openssl rand -hex 16) \
    -e LITELLM_SALT_KEY=$(openssl rand -hex 16) \
    ghcr.io/berriai/litellm:main-latest \
    --config /app/config.yaml --port 4000
```

The proxy now has an admin UI at `http://localhost:4000/ui` (login
with the master key).

---

## Step 2 — Write `litellm-config.yaml`

A minimal config with one virtual model named `smart` that tries
Claude first, falls back to GPT-4o on errors:

```yaml
model_list:
  - model_name: smart
    litellm_params:
      model: claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY
  - model_name: smart
    litellm_params:
      model: gpt-4o
      api_key: os.environ/OPENAI_API_KEY

router_settings:
  routing_strategy: simple-shuffle   # or: usage-based-routing-v2, least-busy
  fallbacks:
    - smart: [gpt-4o]                 # if 'smart' (claude) fails, try gpt-4o
  context_window_fallbacks:
    - smart: [gpt-4o]                 # too-many-tokens? fall back

general_settings:
  drop_params: true                    # silently drop fields a model doesn't support
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

Verify the proxy is healthy:

```bash
curl http://localhost:4000/health/readiness
# → {"status": "healthy", "db": "connected", ...}
```

---

## Step 3 — Smoke-test the gateway

```bash
curl http://localhost:4000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -d '{
       "model": "smart",
       "messages": [{"role": "user", "content": "Hi in one word."}]
     }'
```

You should get a JSON response like:

```json
{
  "id": "chatcmpl-…",
  "choices": [{"message": {"content": "Hello.", ...}}],
  "usage": {...}
}
```

---

## Step 4 — Issue a virtual key for AGCL

You don't want to put the LiteLLM master key into AGCL — it can mint
new keys. Instead, mint a child key with a budget:

```bash
curl http://localhost:4000/key/generate \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "models": ["smart"],
       "max_budget": 100,
       "budget_duration": "30d",
       "key_alias": "agcl-node-1"
     }'
# → {"key": "sk-…", "expires": null, ...}
```

Save the returned `sk-…` value — that's what AGCL will use.

---

## Step 5 — Wire AGCL to LiteLLM

Three env vars in `.env`:

```bash
echo "AGCL_LLM_BASE_URL=http://localhost:4000" >> .env
echo "AGCL_LLM_API_KEY=sk-…the-virtual-key…"  >> .env
echo "AGCL_LLM_MODEL=smart"                    >> .env
```

Or via the web console: **Configuration** tab → env editor → add
each key with **Persist to .env** ON. Then click **Reload .env**.

The chat gateway resolution order is documented in
[agcl/toolkit/registry.py](../../agcl/toolkit/registry.py): if
`AGCL_LLM_BASE_URL` is set, it wins over `VLLM_HOST`, `OLLAMA_HOST`,
or direct cloud calls.

---

## Step 6 — Verify AGCL is routing through it

From the GUI: **Toolkit** tab → find `litellm` in the discover table →
click **Ping**. Should toast `{ok: true, status: 200, ...}`.

From the CLI:

```bash
python main.py toolkit ping litellm
# → {"ok": true, "status": 200, "url": "http://localhost:4000/v1"}

python main.py toolkit chat "Hello in five words." --stream
# tokens stream through LiteLLM → Claude/OpenAI back to your terminal
```

Watch the LiteLLM container logs to confirm:

```bash
docker logs -f litellm
# every request shows model selection + provider response time
```

---

## Step 7 — Add more providers

Edit `litellm-config.yaml`, restart the container:

```yaml
model_list:
  - model_name: smart
    litellm_params:
      model: claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY
  - model_name: smart
    litellm_params:
      model: gpt-4o
      api_key: os.environ/OPENAI_API_KEY
  - model_name: smart
    litellm_params:
      model: together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
      api_key: os.environ/TOGETHER_API_KEY
  - model_name: smart
    litellm_params:
      model: groq/llama-3.1-70b-versatile
      api_key: os.environ/GROQ_API_KEY
  - model_name: smart
    litellm_params:
      model: openai/gpt-4o-mini   # OpenAI-compat — any base_url works
      api_key: os.environ/OPENAI_API_KEY
      api_base: http://vllm:8000/v1  # point at a local vLLM
```

```bash
docker restart litellm
# AGCL needs no changes — it still posts to AGCL_LLM_BASE_URL
```

---

## Common problems

| Symptom                                                  | Fix                                                                                                |
|----------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| `curl /health/readiness` → `db: error`                   | Postgres unreachable. In Docker, use `host.docker.internal` or a shared network.                    |
| AGCL `toolkit ping litellm` → 401                        | Virtual key wrong, or `AGCL_LLM_API_KEY` not picked up. Click **Reload .env**.                      |
| Always falls back to the second provider                 | First provider is rate-limited or returning errors. Check LiteLLM logs.                             |
| Streaming returns plain JSON not SSE                     | Some routes default to non-streaming. Pass `"stream": true` in the request body.                    |
| `model 'smart' not found`                                | `AGCL_LLM_MODEL` doesn't match anything in `model_list`. Use the exact `model_name` value.          |
| 30s timeouts on long responses                            | Add `--timeout=600` to the LiteLLM `docker run` and bump `requests_timeout` in the config.          |
| Spend tracking shows $0                                  | You didn't set `DATABASE_URL`. Without a DB, spend isn't persisted.                                  |

---

## Operating notes

- **Switching defaults**: AGCL still has `DEFAULT_CLOUD=claude|openai`
  but if `AGCL_LLM_BASE_URL` is set, the toolkit gateway path takes
  over — `DEFAULT_CLOUD` is only used when LiteLLM is *not*
  configured.
- **Key rotation**: regenerate the virtual key in LiteLLM's UI, update
  `AGCL_LLM_API_KEY` in `.env`, click **Reload .env**. No node
  restart needed.
- **Cost cap**: set `max_budget` on each virtual key. LiteLLM
  rejects calls when the budget is hit. AGCL's own quotas
  (**Usage & Cost** tab) are a second layer of defense.

---

## What to read next

- Add Ollama / vLLM behind LiteLLM: [ollama.md](ollama.md), [vllm.md](vllm.md)
- The deeper LiteLLM reference (router strategies, callbacks): [docs/integrations/cloud/litellm.md](../integrations/cloud/litellm.md)
- Run LiteLLM itself on Kubernetes: [k8s.md](k8s.md) → adapt for the LiteLLM image.
