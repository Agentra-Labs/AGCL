# Plug in any OpenAI-compatible provider

The OpenAI Chat-Completions API spec is now a de-facto standard.
Anything that speaks `/v1/chat/completions` works with AGCL —
Together AI, Fireworks, Groq, DeepSeek, Perplexity, Replicate,
hosted vLLM endpoints, Anyscale, Bedrock-via-LiteLLM, etc.

> Source: [agcl/toolkit/openai_compat.py](../../agcl/toolkit/openai_compat.py)
> · provider registry: [agcl/usage.py](../../agcl/usage.py) — `register_custom_provider()`

---

## What you're building

```
   AGCL node
   ─────────
       │   POST <base_url>/chat/completions
       │   Authorization: Bearer <key from env var>
       ▼
   ┌────────────────────────────────────────┐
   │  OpenAI-compatible provider             │
   │   ─ Together AI                          │
   │   ─ Fireworks                            │
   │   ─ Groq                                  │
   │   ─ DeepSeek                              │
   │   ─ Perplexity                            │
   │   ─ Any self-hosted vLLM / Ollama         │
   └────────────────────────────────────────┘
```

There are **two ways** to plug a provider in:

| Method               | When to use                                                   |
|----------------------|---------------------------------------------------------------|
| Custom provider     | One-off / personal — just want to use Together for your chats. |
| LiteLLM gateway      | Multiple providers, routing, fallback, spend tracking. See [litellm_gw.md](litellm_gw.md). |

This recipe is for the first option.

## Prerequisites

| Need                                | How                                                            |
|-------------------------------------|----------------------------------------------------------------|
| An API key from the provider        | Sign up on their site                                          |
| Their base URL ending in `/v1`      | from their docs                                                |
| A working AGCL node                  | [web_socket/README.md](../../web_socket/README.md)             |

---

## Step 1 — Save the API key to `.env`

The custom-provider registry stores the **name** of the env var, not
the value. So first put the value in `.env`:

```bash
echo "TOGETHER_API_KEY=tgp_..."         >> .env
# or
echo "GROQ_API_KEY=gsk_..."             >> .env
# or
echo "DEEPSEEK_API_KEY=sk-..."          >> .env
# etc.
```

Reload via Setup → Cloud providers → **Reload .env**, or restart.

---

## Step 2 — Register the provider in AGCL

### From the web console

Setup tab → scroll to "Custom OpenAI-compatible providers".
Fill the four fields:

| Field             | Example for Together AI                                         |
|-------------------|-------------------------------------------------------------------|
| `name`            | `together`                                                       |
| `base_url`        | `https://api.together.xyz/v1`                                    |
| `api_key_env`     | `TOGETHER_API_KEY`                                              |
| `model`           | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo`                  |
| `kind`            | `chat`                                                           |

Click **Add**. The provider is now selectable in the **Chat** tab's
provider dropdown and shows up in **Usage & Cost** → per-provider
summary.

### From the CLI

```bash
curl -X POST http://localhost:9876/node/usage/providers/custom \
     -H "Authorization: Bearer $AGCL_NODE_AUTH" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "together",
       "base_url": "https://api.together.xyz/v1",
       "api_key_env": "TOGETHER_API_KEY",
       "model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
       "kind": "chat"
     }'
```

Or list / delete:

```bash
curl http://localhost:9876/node/usage/providers/custom -H "Authorization: Bearer $AGCL_NODE_AUTH"
curl -X DELETE http://localhost:9876/node/usage/providers/custom/together -H "Authorization: Bearer $AGCL_NODE_AUTH"
```

The registry persists to `.agent_state/custom_providers.json`.

---

## Quick-reference for popular providers

### Together AI

| Field          | Value                                                           |
|----------------|-----------------------------------------------------------------|
| base_url       | `https://api.together.xyz/v1`                                   |
| api_key_env    | `TOGETHER_API_KEY`                                             |
| model          | e.g. `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo`             |

### Fireworks

| Field          | Value                                                           |
|----------------|-----------------------------------------------------------------|
| base_url       | `https://api.fireworks.ai/inference/v1`                         |
| api_key_env    | `FIREWORKS_API_KEY`                                             |
| model          | e.g. `accounts/fireworks/models/llama-v3p1-70b-instruct`        |

### Groq

| Field          | Value                                                           |
|----------------|-----------------------------------------------------------------|
| base_url       | `https://api.groq.com/openai/v1`                                |
| api_key_env    | `GROQ_API_KEY`                                                  |
| model          | e.g. `llama-3.1-70b-versatile`                                  |

### DeepSeek

| Field          | Value                                                           |
|----------------|-----------------------------------------------------------------|
| base_url       | `https://api.deepseek.com/v1`                                   |
| api_key_env    | `DEEPSEEK_API_KEY`                                              |
| model          | e.g. `deepseek-chat`                                            |

> DeepSeek also has a *balance* endpoint AGCL knows how to call.
> Setup → Cloud providers will show your remaining credit.

### Perplexity

| Field          | Value                                                           |
|----------------|-----------------------------------------------------------------|
| base_url       | `https://api.perplexity.ai`                                     |
| api_key_env    | `PERPLEXITY_API_KEY`                                            |
| model          | e.g. `llama-3.1-sonar-large-128k-online`                        |

### Self-hosted vLLM / TGI / Ollama

| Field          | Value                                                           |
|----------------|-----------------------------------------------------------------|
| base_url       | `http://your-host:8000/v1` (or `:11434/v1` for Ollama)          |
| api_key_env    | `VLLM_API_KEY` (set it to `EMPTY` if the server doesn't enforce auth) |
| model          | whatever you loaded                                              |

See [vllm.md](vllm.md), [tgi.md](tgi.md), [ollama.md](ollama.md) for
the server setup; this recipe is just the AGCL registration step.

---

## Step 3 — Verify it works

In the **Chat** tab, set the **Provider** dropdown to your new
provider's name and send a message. The local prefix bubble still
comes from `LOCAL_MODEL_PATH`; the cloud reply now comes from your
new provider.

From the CLI:

```bash
curl http://localhost:9876/node/setup/providers/check \
     -H "Authorization: Bearer $AGCL_NODE_AUTH" \
     -H "Content-Type: application/json" \
     -d '{"provider": "deepseek"}'
```

For DeepSeek + OpenAI you also get balance / quota in the same call:

```json
{
  "ok": true,
  "status": "ok",
  "message": "key valid — 7 models accessible",
  "quota": {
    "ok": true,
    "available": true,
    "balances": [{"currency": "CNY", "total_balance": "9.95", ...}]
  }
}
```

---

## Step 4 — Set a quota

Once it's added, set a hard cost cap so a buggy session can't burn
your wallet:

**GUI**: Usage & Cost → Quotas → Provider `together`, Max USD `5`,
Period `monthly`, Apply quota.

**CLI**:

```bash
curl -X PUT http://localhost:9876/node/usage/quota/together \
     -H "Authorization: Bearer $AGCL_NODE_AUTH" \
     -H "Content-Type: application/json" \
     -d '{"max_credit_usd": 5, "period": "monthly"}'
```

When the cap is hit, AGCL refuses to call the provider until the
period rolls over or you raise the cap. See [docs/security.md
§8](../security.md#8-quotas--the-hard-cap-and-its-limit) for the
known race-condition window.

---

## Common problems

| Symptom                                              | Fix                                                                                            |
|------------------------------------------------------|------------------------------------------------------------------------------------------------|
| Provider doesn't appear in the chat dropdown         | The registration is per-process — the node loaded the registry at startup. Click Setup → Reload .env, or restart. |
| 401 on every call                                    | `api_key_env` typo, or the env var isn't actually exported. Test with `echo $TOGETHER_API_KEY`. |
| 404 / "model not found"                              | The `model` field doesn't match anything the provider exposes. Hit `GET <base_url>/models` to list available IDs. |
| `Authentication required` from a self-hosted server  | Set `VLLM_API_KEY` to whatever the server expects (often `EMPTY` for unauthenticated dev servers). |
| Output is gibberish                                  | Wrong model id for this base_url, or the provider's "OpenAI-compat" mode is incomplete (some only support `/completions`, not `/chat/completions`). |
| Provider has a non-`/v1` path                        | Use the exact base URL the provider documents. Perplexity is `https://api.perplexity.ai` (no `/v1`). |

---

## Operating notes

- The custom-provider registry is **per-machine** — provider records
  live in `.agent_state/custom_providers.json`. Export with
  **Configuration → Config bundle → Download** to share with a
  teammate (API keys are never in the bundle).
- AGCL's per-provider usage tracking + quotas work identically for
  custom providers as for OpenAI/Anthropic — see Usage & Cost tab.
- The unified surface means switching providers is a Setup-tab click;
  callers don't change.

---

## What to read next

- LiteLLM proxy for multi-provider routing/fallback: [litellm_gw.md](litellm_gw.md)
- Provider live-check API reference: [docs/web-console.md](../web-console.md#cloud-providers)
- Run your own OpenAI-compat server: [vllm.md](vllm.md), [ollama.md](ollama.md), [tgi.md](tgi.md)
