# Endpoint reference

Every HTTP route the node exposes. All endpoints sit under `/node/*`
except for liveness and the existing `/health`, `/pressure`, `/patterns`
from the main agent (which still work and are also auth-gated when the
node is up).

> Part of the **[GUI integration guide](../gui.md)**.
> See also: [Connect](connect.md) · [SSE events](sse-events.md) ·
> [Config map](config-map.md) · [TypeScript client](typescript-client.md)

---

## Auth + liveness

### `GET /node/health`  (no auth)

Liveness probe. Use for "is the node running?" checks.

```http
GET /node/health
```

```json
{
  "ok": true,
  "service": "agcl-node",
  "version": "1",
  "issued_at": 1715200000.123
}
```

### `POST /node/auth/verify`

Confirm the bearer key is valid.

```http
POST /node/auth/verify
Authorization: Bearer 8f3e…
```

`200` → `{"ok": true, "issued_at": 1715200000.123}`
`401` → `{"detail": "invalid token"}`

---

## System info

### `GET /node/info`

Return everything the GUI needs for the dashboard "what's installed"
view: providers, MAS shape, local model path, saved topics count.

```json
{
  "service": "agcl-node",
  "platform": {
    "state_dir":     ".agent_state",
    "default_cloud": "claude",
    "providers":     {"openai": false, "claude": true}
  },
  "mas": {
    "pattern": "sequential",
    "rounds":  2,
    "device":  "cpu",
    "dtype":   "float32",
    "agents": [
      {"backend": "hf", "model": "Qwen/Qwen2.5-0.5B-Instruct", "role": "planner"},
      {"backend": "hf", "model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "role": "solver"}
    ]
  },
  "local_model": {
    "path": "models/SmolLM2-135M.Q2_K.gguf",
    "n_ctx": 2048,
    "n_gpu_layers": 0,
    "n_threads": 12,
    "prefix_word_count": 4
  },
  "topics_count": 3
}
```

---

## Editable config

### `GET /node/config`

Returns the **full editable knob tree** in one call. The GUI's
"settings page" can render straight from this. Field-by-field reference
in [config-map.md](config-map.md).

```json
{
  "cloud": {
    "default":        "claude",
    "openai_model":   "gpt-4o",
    "claude_model":   "claude-sonnet-4-20250514",
    "has_openai_key": false,
    "has_claude_key": true
  },
  "local_model": {
    "path":               "models/SmolLM2-135M.Q2_K.gguf",
    "n_ctx":              2048,
    "n_gpu_layers":       0,
    "n_threads":          12,
    "prefix_word_count":  4
  },
  "mas": {
    "pattern":  "sequential",
    "rounds":   2,
    "device":   "cpu",
    "dtype":    "float32",
    "agents":   [ … ]
  },
  "session_defaults": {
    "switch_threshold":    0.6,
    "retrieval_threshold": 0.75,
    "stage1_steps":        30,
    "stage2_steps":        20,
    "n_reformulations":    6,
    "max_new_tokens":      128,
    "cloud_continue":      false,
    "prefix_tokens":       12,
    "persist":             true
  },
  "storage": {
    "state_dir":          ".agent_state",
    "idle_flush_sec":     180,
    "session_ttl_sec":    3600,
    "max_context_tokens": 6000
  }
}
```

### `PATCH /node/config`

Partial update. Send only the fields the user changed. The node
persists each to `.env` (so they survive restart) and updates
`os.environ` (so reads are immediate).

```http
PATCH /node/config
Content-Type: application/json
Authorization: Bearer …

{
  "default_cloud": "openai",
  "openai_model":  "gpt-4o-mini",
  "mas_rounds":    3
}
```

```json
{
  "ok": true,
  "applied": ["DEFAULT_CLOUD", "OPENAI_MODEL", "MAS_ROUNDS"],
  "requires_restart": true
}
```

If `requires_restart: true`, show a "restart node to apply" badge in
the GUI (or call `POST /node/mas/rebuild` to re-load just the MAS
without restarting the process; see below).

---

## `mas.json` (the agent recipe)

### `GET /node/config/mas`

Returns the current `mas.json` (or the in-config default if no file
exists yet).

```json
{
  "source": "mas.json",
  "data": {
    "agents": [
      {"backend": "hf", "model": "Qwen/Qwen2.5-0.5B-Instruct", "role": "planner",
       "device": "cpu", "dtype": "float32"},
      {"backend": "hf", "model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "role": "solver",
       "device": "cpu", "dtype": "float32"}
    ]
  }
}
```

### `PUT /node/config/mas`

Overwrite `mas.json`. Backs up the previous file to `mas.json.bak`.
Requires a process restart (or a `POST /node/mas/rebuild`) before the
new agents are actually loaded.

```http
PUT /node/config/mas
Content-Type: application/json

{
  "agents": [
    {"backend": "gguf", "model": "models/my.gguf", "role": "planner",
     "n_ctx": 2048, "n_threads": 8},
    {"backend": "hf", "model": "Qwen/Qwen2.5-0.5B-Instruct", "role": "solver",
     "device": "cpu", "dtype": "bfloat16"}
  ]
}
```

```json
{"ok": true, "path": "mas.json", "agents": 2, "requires_restart": true}
```

---

## Recursive MAS — runtime

### `POST /node/mas/run`

One-shot. Runs a turn through the multi-turn `RecursiveSession`,
returns the full answer once everything is done. Blocking — for live
training/answer streaming use `/mas/stream`.

```http
POST /node/mas/run
Content-Type: application/json

{
  "session_id":  "default",
  "message":     "explain quantum entanglement simply",
  "force_cloud":    false,
  "force_continue": false,

  "switch_threshold":     0.6,
  "retrieval_threshold":  0.75,
  "stage1_steps":         30,
  "stage2_steps":         20,
  "n_reformulations":     6,
  "max_new_tokens":       128,
  "cloud_continue":       false,
  "prefix_tokens":        12,
  "persist":              true,
  "cloud_provider":       "claude"
}
```

Only `message` is required. The session-defaults fields are
**sticky on the session**: they're only honored when the session is
*first created* (the first turn for that `session_id`). To change them
later, delete the session via `DELETE /node/mas/sessions/{sid}` and
recreate it.

Per-turn flags `force_cloud` / `force_continue` are honored every
call.

```json
{
  "ok": true,
  "session_id": "default",
  "topic_id":   "92ff1629f157",
  "answer":     "Quantum entanglement is a phenomenon where two particles…"
}
```

### `POST /node/mas/stream`

Same body as `/mas/run`, returns an SSE stream. **Use this for
anything user-facing** — it surfaces every internal step so the GUI
can show progress. See [SSE events](sse-events.md) for the event
reference.

```http
POST /node/mas/stream
Content-Type: application/json
Accept: text/event-stream

{ "session_id": "default", "message": "explain quantum entanglement" }
```

### `POST /node/mas/rebuild`

Drop the loaded MAS singleton + all sessions; the next `/mas/run` will
rebuild from the current config. Use this after `PUT /config/mas` or a
`PATCH /config` that returned `requires_restart: true` if you don't
want to actually restart the process. (HF model weights stay in the
filesystem cache so rebuild is reasonably quick - a few seconds per
agent.)

```json
{"ok": true}
```

---

## Training control + latent-space inspection

These endpoints let a GUI **halt, pause, and resume training mid-run**,
inspect the latent vector currently flowing through the recursive MAS
loop, and discover plugin-registered routes. They're the pluggable
control surface for the recursive feature; the full reference lives in
[../plugins.md](../plugins.md).

| Endpoint | Effect |
|---|---|
| `POST /node/mas/sessions/{sid}/pause`  | Block the training loop at its next step boundary (resumable) |
| `POST /node/mas/sessions/{sid}/resume` | Unblock a paused loop |
| `POST /node/mas/sessions/{sid}/halt`   | Halt training; the SSE stream emits `halted` then `done` |
| `GET  /node/mas/sessions/{sid}/status` | Return the current control state (`stage`, `step`, `paused`, `halted`) |
| `GET  /node/mas/sessions/{sid}/latent` | Snapshot the most recently observed loop latent (truncated) |
| `GET  /node/plugins`                   | List loaded plugins + their load status |

A control request takes effect at the next training step boundary, so
worst-case latency is one step (typically tens of milliseconds for
Stage A, low seconds for Stage B). The corresponding SSE events
emitted on `/node/mas/stream` are `paused`, `resumed`, `halted`, and
`halted_done`; see endpoint.md for payload shapes.

---

## MAS sessions

### `GET /node/mas/sessions`

```json
{
  "sessions": [
    {
      "session_id": "default",
      "topic_id":   "92ff1629f157",
      "topic_seed": "explain quantum entanglement",
      "trained":    true,
      "n_turns":    4
    }
  ]
}
```

### `GET /node/mas/sessions/{session_id}`

Full session state including chat history.

```json
{
  "session_id": "default",
  "topic_id":   "92ff1629f157",
  "topic_seed": "explain quantum entanglement",
  "trained":    true,
  "history": [
    {"role": "user", "content": "explain quantum entanglement"},
    {"role": "assistant", "content": "Quantum entanglement is…"}
  ]
}
```

### `DELETE /node/mas/sessions/{session_id}`

Drops the in-memory session. The trained-topic file on disk is
**untouched** — a future session whose seed matches will retrieve it.

---

## Topics (the trained-state index)

### `GET /node/topics`

```json
{
  "topics": [
    {
      "topic_id":      "92ff1629f157",
      "seed_question": "explain quantum entanglement",
      "signature":     {"dims": [896, 2048], "roles": ["planner","solver"], "n_rounds": 2},
      "saved_at":      1715200000.123,
      "centroid_dim":  896,
      "n_reformulations": 6,
      "stage1_final":  0.0683,
      "stage2_final":  3.05
    }
  ]
}
```

The GUI can show this as a list of "things this PC has been trained
on." Returning to a similar question reuses the saved file
automatically — no GUI action needed.

### `DELETE /node/topics/{topic_id}`

Removes the saved files (`meta.json`, `centroid.pt`, `links.pt`).
Returns `404` if not present, `200` otherwise.

---

## Main-agent chat (the local-prefix-cloud-continuation flow)

These are passthroughs to the existing `/chat`-style routes, re-exposed
under `/node/*` so the GUI only has to know one prefix.

### `POST /node/chat/{session_id}`

Streams an SSE response. Payload mirrors the existing
`/chat/{session_id}`:

```json
{
  "session_id":    "default",
  "message":       "what's the time complexity of quicksort?",
  "provider":      "claude",
  "recovery_mode": "natural"
}
```

SSE events:

```
data: {"type": "prefix", "text": "Quicksort runs", "local_ms": 87}
data: {"type": "chunk",  "text": " in average-case O(n log n)…"}
data: {"type": "chunk",  "text": " but O(n²) worst case."}
data: {"type": "done",   "pressure": {"rate_ratio": 0.05, "avg_latency_sec": 0.4}}
```

`type: prefix` arrives once at the start (the local llama.cpp
opener), then many `type: chunk` events as the cloud streams,
finishing with one `type: done`.

### `GET /node/sessions`, `GET /node/sessions/{id}`, `DELETE /node/sessions/{id}`

List / view / flush main-agent chat sessions.

```json
{
  "session_id":    "default",
  "message_count": 12,
  "messages":      [ … ],
  "created_at":    "2025-…",
  "last_active":   "2025-…"
}
```

---

## Generic config exposure

The curated `/node/config` patch surface only covers the headline knobs
listed in [`config-map.md`](config-map.md). For everything else (env
vars added by plugins, toolkit settings, future additions) use these
generic routes — no schema patch required when a new knob is added.

### `GET /node/config/env?prefix=AGCL_`

Returns every env var the process sees (optionally filtered by
`prefix`), with secret-shaped names masked:

```json
{
  "env": {
    "AGCL_LLM_BASE_URL": "http://localhost:4000",
    "ANTHROPIC_API_KEY": "***(108 chars)",
    "MAS_ROUNDS":        "2"
  },
  "count": 3
}
```

### `PATCH /node/config/env`

```http
PATCH /node/config/env
{
  "updates":         {"DEFAULT_CLOUD": "openai", "MAS_ROUNDS": "3"},
  "persist":         true,
  "allowlist_only":  true
}
```

By default only allowlisted keys (the same set
[`agcl/config_bundle.py`](../../agcl/config_bundle.py) exports) are
accepted; arbitrary keys come back in `rejected`. With `persist:true`
(default) the change is written to `.env` so it survives restart.

### `GET /node/config/all`

Returns the full **config bundle** (env + `mas.json` + model file
pointers + HF model ids + extras_required), the same shape `agcl
config export` produces. Pair with `POST /node/config/import` to
ingest a teammate's bundle.

### `POST /node/config/import?apply=true`

Body: a config bundle. Without `?apply=true` it returns a dry-run hint
report (missing files / HF models / pip extras / unset env vars). With
`?apply=true` it writes `.env` + `mas.json` (with backup).

### `GET /node/runtime/info`

Cheap snapshot for "running on" badges:

```json
{
  "python":    "3.11.6",
  "platform":  "Linux-6.5.0-x86_64",
  "machine":   "x86_64",
  "pid":       12345,
  "cwd":       "/home/...",
  "env_count": 87,
  "gpus":      ["NVIDIA GeForce RTX 4090, 24576 MiB"],
  "toolkit":   { "litellm": {...}, "ollama": {...}, ... }
}
```

---

## Plugin reload

### `POST /node/plugins/reload`

Re-discover and re-register every plugin under the
`AGCL_PLUGINS_DIR` (default `plugins/`). Returns the same shape as
`GET /node/plugins`. Useful while developing a plugin — caveat: routes
registered by previous loads aren't removed (FastAPI doesn't expose a
clean way to do that), so add a route once and edit it in place.

---

## Mini-trainer (`/node/mini/*`)

Optional, toggleable, pause/resume-able tiny model that trains in the
background off latents + reformulations captured during normal AGCL
use. Full feature reference: [`../minimodel.md`](../minimodel.md).

| Endpoint | Effect |
|---|---|
| `GET    /node/mini/status` | trainer state (enabled, running, step, last loss) |
| `GET    /node/mini/config` | current trainer config |
| `PATCH  /node/mini/config` | partial-update config; `lr` is hot-applied, arch fields force a rebuild |
| `POST   /node/mini/start` / `stop` / `pause` / `resume` | thread lifecycle |
| `POST   /node/mini/test` | `{prompt, max_new?, temperature?}` -> sample generation |
| `POST   /node/mini/checkpoint` | force a save right now |
| `GET    /node/mini/presets` | available strategies / attention masks / archs |

---

## Toolkit (`/node/toolkit/*`)

The infra/inference adapter surface. Full doc:
[`../integrations/cloud.md`](../integrations/cloud.md).

| Endpoint | Effect |
|---|---|
| `GET  /node/toolkit/discover` | which adapters are configured + importable |
| `GET  /node/toolkit/ping/{adapter}` | reachability check (`ollama` / `vllm` / `litellm` / `tgi` / `redis` / `s3` / `cloudflare` / `discord` / `docker` / `k8s` / `gcp`) |
| `POST /node/toolkit/chat` | gateway-aware chat completion (LiteLLM > vLLM > Ollama). `stream:true` returns SSE |
| `PUT  /node/toolkit/state` / `GET /node/toolkit/state/{id}` / `DELETE /node/toolkit/state/{id}` | distributed state (Redis when configured, else local files) |
| `PUT  /node/toolkit/checkpoint` / `GET /node/toolkit/checkpoint/{topic_id}/{name}` / `DELETE /node/toolkit/checkpoint/{topic_id}` / `GET /node/toolkit/checkpoints` | S3-compatible checkpoint store (R2/MinIO/B2/AWS or local) |
| `POST /node/toolkit/relay/publish` | push an event to the Cloudflare Worker relay |
| `POST /node/toolkit/manifest/docker` / `manifest/k8s` / `manifest/gcp` | emit Dockerfile+Compose / Helm chart / Cloud Run service.yaml |
| `GET/POST /node/toolkit/webrtc/...` | offer / answer / ICE signaling (in-memory store) |

---

## Multica plugin (`/node/multica/*`)

Auto-loaded when [`plugins/multica.py`](../../plugins/multica.py) is
present. Full doc: [`../integrations/multica.md`](../integrations/multica.md).

| Endpoint | Effect |
|---|---|
| `GET  /node/multica/health` | `{"agcl": "ready", ...}` — pre-flight before assigning tasks |
| `GET  /node/multica/skills?workdir=<path>` | read injected skills from `<workdir>/.agcl/context.md` and `.agcl/skills/*.md` |
| `POST /node/multica/run` | run a Multica task body, stream the unified jsonl event taxonomy as SSE |
