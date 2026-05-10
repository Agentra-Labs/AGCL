# Dashboard — token usage, quotas, API management

AGCL ships a dashboard at `/node/dashboard` that surfaces the same data
the new `/node/usage/*` endpoints expose. It's a single HTML page
shipped from [`agcl/dashboard.py`](../agcl/dashboard.py); no external
build, no CDN, no JS framework.

> Sister docs:
> - **[web-console.md](web-console.md)** — the larger bundled
>   `/web_socket/` console covers everything this dashboard does *and*
>   the rest of the node API (chat, MAS, mini-trainer, toolkit, plugins,
>   diagnostics, deploy manifests). Use that for the full UI; this
>   in-server dashboard is the lightweight, zero-deps fallback.
> - **[gui.md](gui.md)** — GUI integration contract for building your own.
> - **[gui/endpoints.md](gui/endpoints.md)** — full endpoints reference.

---

## Open it

```bash
python main.py node --port 9876
# then in a browser:
open http://localhost:9876/node/dashboard
```

The page is auth-public (the static HTML loads without a bearer), but
**every data fetch the page makes IS auth-gated**. The first time the
page loads, paste the bearer key the node printed; it's stored in
`localStorage` for that origin and used for every subsequent request.

---

## What the dashboard shows

Three tabs:

### 1. Overview

- **Provider cards** — one per configured backend (Claude, OpenAI, plus
  any registered custom or LiteLLM-gateway entries). Each card shows:
  model id, configured/no-key status, lifetime tokens, lifetime cost
  (estimated), and quota usage (% of cap, remaining).
- **Token graph (last hour)** — solid bars are *chat-path* tokens
  (regular session turns); hollow bars are *knowledge-formation* tokens
  (cloud-teacher bootstrap, reformulations, topic-switch judgements,
  context summarization).
- **Sessions table** — one row per session, with chat tokens,
  knowledge tokens, total cost, call count, last-seen timestamp, and a
  jump-into-detail link.

### 2. Sessions

Per-session detail view. Two graphs (chat tokens / knowledge tokens
over the last 24h) plus a per-kind breakdown table (in/out/total/calls/
cost split by `kind`). Pick a session from the overview table or paste
a session id directly.

### 3. API management

Two forms:

- **Set quota** — pick a provider, set `max_tokens` *or* `max_credit_usd`,
  pick a period (`lifetime` / `daily` / `monthly`). Once saved, every
  cloud call is gated by `agcl.usage.check_quota()` — exceeding the cap
  raises `QuotaExceeded` and the call never goes out.
- **Custom OpenAI-compatible provider** — register a name + base URL +
  env-var name (where the API key lives at runtime) + default model.
  The entry persists in `<STATE_DIR>/custom_providers.json`. Useful
  for Groq, Together, DeepInfra, Fireworks, a private vLLM, etc.

---

## How tokens are categorized

Every cloud call AGCL makes is recorded with a `kind` tag:

| `kind` | Where it's emitted | What it represents |
|---|---|---|
| `chat` | [`agcl/cloud.py`](../agcl/cloud.py)'s `stream_continuation()` | Tokens spent answering user turns (the prefix-continuation path) |
| `knowledge` | [`agcl/recursive/auto_train.py`](../agcl/recursive/auto_train.py)'s `_cloud_call()` | Bootstrap teacher answers, reformulations, topic-switch yes/no judgements |
| `summarize` | (reserved) context-window compression | Context that got compressed because the session went over `MAX_CONTEXT_TOKENS` |
| `other` | catch-all | Anything else — set explicitly by callers |

The split shows up in the dashboard as the two-color bar chart, the
sessions-table columns, and the per-session detail view.

---

## How quotas work

1. **Set** via the dashboard form, the npm client (`setUsageQuota`),
   or `PUT /node/usage/quota/{provider}` with body
   `{max_tokens?: int, max_credit_usd?: float, period: "lifetime"|"daily"|"monthly"}`.
2. **Persisted** to `<STATE_DIR>/usage_quotas.json`.
3. **Checked** before each cloud call via `agcl.usage.check_quota(provider)`.
   If the period's running total (read from the same in-memory ring +
   `usage.jsonl` log) is >= the cap, `QuotaExceeded` is raised and the
   call doesn't go out.
4. **Cost estimation** uses a built-in pricing table
   (`agcl.usage.DEFAULT_PRICING`), overridable via env vars
   `PRICING_<PROVIDER>_INPUT` / `_OUTPUT` (USD per 1k tokens).
   These are estimates — never billing-accurate.

---

## How custom providers fit in

When you add a custom provider on the dashboard:

```jsonc
// stored as {name: spec}
{
  "groq": {
    "name":        "groq",
    "base_url":    "https://api.groq.com/openai/v1",
    "api_key_env": "GROQ_API_KEY",
    "model":       "llama3-8b-8192",
    "kind":        "openai-compatible"
  }
}
```

It surfaces:

- as a provider card on the overview
- in the API-management quota selector
- (importantly) does **not** auto-route inference to it — that's
  controlled by `AGCL_LLM_BASE_URL` (LiteLLM gateway) or
  `OLLAMA_HOST` / `VLLM_HOST` for direct connections. The custom
  registry is metadata: a list of providers you'd like to track and
  put quotas on. Pair it with a LiteLLM proxy entry to actually route
  traffic.

---

## Build your own dashboard

Every backing endpoint is documented in
[gui/endpoints.md](gui/endpoints.md#usage-quotas--custom-providers).
The shipped HTML is a reference implementation, not the only option:

- **From scratch (any framework):** hit the same `/node/usage/*`
  endpoints. Each returns plain JSON.
- **Vue 3:** the npm client ships composables at `@agcl/client/vue` —
  `useAgclUsage`, `useAgclTimeseries`, `useAgclMas`, `useAgclChat`. See
  [integrations/npm.md](integrations/npm.md#vue-composables).
- **React/Svelte/Solid:** the async-iterator surface
  (`streamMas`, `chatStream`, `usageTimeseries`) plays nicely with any
  reactive primitive. Wrap in your own hook in 5 lines —
  [npm.md](integrations/npm.md) has a 5-line React example.

---

## Endpoint summary

| Endpoint | Effect |
|---|---|
| `GET  /node/dashboard` | the static SPA itself |
| `GET  /node/usage/summary` | provider snapshot + sessions table in one call |
| `GET  /node/usage/providers` | provider snapshot only |
| `GET  /node/usage/sessions` | every session, sorted by last_seen |
| `GET  /node/usage/sessions/{sid}` | per-kind breakdown for one session |
| `GET  /node/usage/timeseries?session_id=...&provider=...&kind=...&bucket_sec=60&lookback_sec=3600` | bucketed token + cost series for graphs |
| `GET  /node/usage/quotas` | every quota currently set |
| `PUT  /node/usage/quota/{provider}` | set / replace a quota |
| `DELETE /node/usage/quota/{provider}` | clear a quota |
| `GET  /node/usage/providers/custom` | list registered custom providers |
| `POST /node/usage/providers/custom` | register a new custom provider |
| `DELETE /node/usage/providers/custom/{name}` | unregister |

---

## What stays in memory vs. on disk

| Thing | Lives in | Notes |
|---|---|---|
| Recent calls (last 5,000) | RAM ring + `<STATE_DIR>/usage.jsonl` (append-only) | Replays the tail into RAM at process start |
| Quotas | `<STATE_DIR>/usage_quotas.json` | Reloaded on first usage call |
| Custom providers | `<STATE_DIR>/custom_providers.json` | Same |

Truncate `usage.jsonl` if you ever want to reset history; the ring
empties on next process restart.
