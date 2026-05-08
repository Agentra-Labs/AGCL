# GUI integration - wiring AGCL node into a UI layer

This doc is for whoever's writing the **GUI / web frontend** that talks
to a user's local AGCL (Agentic CLI) install. Everything you need to
plug in is here: the auth flow, every endpoint, every editable config,
the SSE event format, the CORS posture, and pointers to the pluggable
control surface for halting / pausing training and inspecting the
latent space.

> **Where you are:** the integration contract.
> Friendlier docs:
> - **[guide.md](guide.md)** - beginner setup
> - **[configuration.md](configuration.md)** - every config knob in plain
>   English
> - **[endpoint.md](endpoint.md)** - **pluggable endpoints**: control
>   recursive MAS (halt / pause / resume), inspect latent space, and
>   register your own routes via `plugins/`
> - **[training.md](training.md)** - what auto-training does
> - **[recursive_mas_setup.md](recursive_mas_setup.md)** - terse
>   technical reference
> - **[main.md](main.md)** - per-file code reference

---

## Two ways to drive AGCL

AGCL ships in two modes; the integration story differs depending on
which one you target:

| Mode | What it is | When to use |
|---|---|---|
| **TUI shell** (default) | `python main.py` opens a sustained, arrow-key-navigable terminal app with linted slash commands | End users on a single PC; no HTTP needed |
| **Node server** (opt-in) | `python main.py node --port 9876` exposes the same engine over HTTP+SSE with bearer auth | A separate GUI talking to a user's PC |

The server is a feature, **not the default startup path**. Everything
below targets the node-server mode since that's what a GUI integrates
with. The TUI hits the same Python APIs in process, so every feature
described here is also reachable from the shell as a slash command.

---

## How a user connects their PC to your GUI

1. User opens a terminal and runs:

   ```bash
   python main.py node --port 9876
   ```

2. Their terminal prints a one-time auth key:

   ```
   ============================================================
     AGCL node - ready
   ============================================================
     bind:        127.0.0.1:9876
     cors:        *
     auth key:    8f3e_…_some_long_url_safe_token
     paste the auth key above into your GUI to authorize this PC.
     every request must send:  Authorization: Bearer <key>
     killing this process invalidates the key.
   ============================================================
   ```

3. User pastes that key into your GUI (single text field).
4. Your GUI hits `http://localhost:9876/node/auth/verify` with
   `Authorization: Bearer <key>`. If 200, store the key + base URL in
   GUI session state. If 401, show "invalid key, paste again".
5. From this point on, every request to the node sends that bearer.

The user can change `--port` to any value — your GUI should let them
configure both the host (default `localhost`) and port. You may also
let them set `--auth-key` for a stable key across restarts.

> **Killing the node process invalidates the key.** Show a "node
> disconnected" state if any request returns network-error, and prompt
> for a fresh key.

---

## Connection settings the GUI should expose

| GUI field | Maps to | Default | Notes |
|---|---|---|---|
| Host | URL host | `localhost` | Usually local; can be a LAN IP if user ran `--bind 0.0.0.0` |
| Port | URL port | `9876` | User-editable on the node CLI |
| Auth key | `Authorization: Bearer …` | (none) | Pasted from the node terminal |

Build URLs as `http://<host>:<port>` + the paths below.

---

## CORS posture

The node defaults to `Access-Control-Allow-Origin: *` and accepts
`Authorization` and `Content-Type` headers. This is safe because all
auth flows through the bearer token — no cookies, no
`allow_credentials`. Every request preflights cleanly.

If the GUI is hosted on a specific origin, the user can lock the node
down with `--cors https://your-gui.example.com`.

The browser sees:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Authorization, Content-Type
```

---

## Endpoint reference

All endpoints sit under `/node/*` except for liveness and the existing
`/health`, `/pressure`, `/patterns` from the main agent (which still
work and are also auth-gated when the node is up).

### Auth + liveness

#### `GET /node/health`  (no auth)

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

#### `POST /node/auth/verify`

Confirm the bearer key is valid.

```http
POST /node/auth/verify
Authorization: Bearer 8f3e…
```

`200` → `{"ok": true, "issued_at": 1715200000.123}`
`401` → `{"detail": "invalid token"}`

---

### System info

#### `GET /node/info`

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

### Editable config

#### `GET /node/config`

Returns the **full editable knob tree** in one call. The GUI's
"settings page" can render straight from this.

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

#### `PATCH /node/config`

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

### `mas.json` (the agent recipe)

#### `GET /node/config/mas`

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

#### `PUT /node/config/mas`

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

### Recursive MAS — runtime

#### `POST /node/mas/run`

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

#### `POST /node/mas/stream`

Same body as `/mas/run`, returns an SSE stream. **Use this for
anything user-facing** — it surfaces every internal step (topic gate,
retrieval, bootstrap, training step-by-step, generation) so the GUI
can show progress.

```http
POST /node/mas/stream
Content-Type: application/json
Accept: text/event-stream

{ "session_id": "default", "message": "explain quantum entanglement" }
```

Response is a stream of `data: {…}\n\n` lines. The GUI should parse
each event as JSON and dispatch on `event`.

#### Event reference

| `event` | Fields | When | What the GUI should do |
|---|---|---|---|
| `start` | `session_id` | first event | Open a "thinking" state |
| `turn_start` | `force_cloud`, `force_continue` | once per turn | — |
| `topic_gate` | `sim`, `threshold`, `candidate` | only on follow-ups | Optional debug indicator |
| `topic_switch_confirmed` | `switched` (bool) | when gate fires | Show "topic switch detected" badge |
| `retrieval_lookup` | — | new topic | "Looking up similar past topics…" |
| `retrieval_hit` | `topic_id`, `sim`, `seed` | found a saved topic | "Reusing trained topic from … (sim=0.81)" |
| `retrieval_miss` | `threshold` | no match | — |
| `bootstrap_start` | `is_switch` | begin training | "Asking cloud for a teacher answer…" |
| `bootstrap_done` | `answer_chars`, `n_reformulations` | cloud done | "Training on answer (412 chars)" |
| `stage1_start` | `total_steps` | begin Stage A | Begin progress bar |
| `stage1_step` | `step`, `total_steps`, `loss` | every step | Update Stage A progress + loss line |
| `stage1_done` | `first_loss`, `final_loss` | end Stage A | "Stage A: 0.92 → 0.07" |
| `stage2_start` | `total_steps` | begin Stage B | Begin Stage B progress bar |
| `stage2_step` | `step`, `total_steps`, `loss` | every step | Update Stage B progress |
| `stage2_done` | `first_loss`, `final_loss` | end Stage B | "Stage B: 8.4 → 3.1" |
| `stage2_skipped` | `reason` | GGUF or short answer | "Stage B skipped: GGUF final agent" |
| `topic_persisted` | `topic_id` | after training | "Saved topic …" |
| `topic_persist_failed` | `error` | save failed | Toast warning |
| `generation_start` | `mode` (`local`/`continuator`/`cloud_only`) | begin output | Switch to "writing" state |
| `prefix` | `text` | continuator mode | Display the local prefix |
| `prefix_dropped` | `prefix` | continuator mode, prefix degenerate | "Local prefix discarded" |
| `fallback_to_cloud` | `local_output` | local was junk | Toast "fell back to cloud" |
| `cloud_only_start` / `cloud_only_done` | `chars` | force_cloud | — |
| `generation_done` | `mode`, `chars` | local done | — |
| `answer_ready` | `chars` | bootstrap path | Cloud answer became the response |
| `answer` | `text`, `topic_id` | always | **The final answer string** |
| `error` | `type`, `message` | on exception | Show error banner |
| `done` | — | last event | Close the stream UI |

The order is roughly:
`start → turn_start → (topic gate)? → (retrieval | bootstrap+training)? →
generation_start → … → answer → done`.

The first turn of a brand-new topic emits **all** of `bootstrap_*`,
`stage1_*`, `stage2_*`, `topic_persisted`, `answer`, `done` —
that's the slow path (~1–3 min on CPU). Follow-ups skip everything
between `turn_start` and `generation_start`.

#### `POST /node/mas/rebuild`

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

### Training control + latent-space inspection

These endpoints let a GUI **halt, pause, and resume training mid-run**,
inspect the latent vector currently flowing through the recursive MAS
loop, and discover plugin-registered routes. They're the pluggable
control surface for the recursive feature; the full reference lives in
**[endpoint.md](endpoint.md)**.

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

### MAS sessions

#### `GET /node/mas/sessions`

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

#### `GET /node/mas/sessions/{session_id}`

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

#### `DELETE /node/mas/sessions/{session_id}`

Drops the in-memory session. The trained-topic file on disk is
**untouched** — a future session whose seed matches will retrieve it.

---

### Topics (the trained-state index)

#### `GET /node/topics`

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

#### `DELETE /node/topics/{topic_id}`

Removes the saved files (`meta.json`, `centroid.pt`, `links.pt`).
Returns `404` if not present, `200` otherwise.

---

### Main-agent chat (the local-prefix-cloud-continuation flow)

These are passthroughs to the existing `/chat`-style routes, re-exposed
under `/node/*` so the GUI only has to know one prefix.

#### `POST /node/chat/{session_id}`

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

#### `GET /node/sessions`, `GET /node/sessions/{id}`, `DELETE /node/sessions/{id}`

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

## Full editable-config map

Every knob the GUI can show + edit, with which env var it lives in,
whether changing it requires a rebuild, and the default.

| GUI field | env var | Restart? | Default | Notes |
|---|---|:---:|---|---|
| **Cloud — provider** | `DEFAULT_CLOUD` | no | `claude` | `claude` or `openai` |
| **Cloud — OpenAI model** | `OPENAI_MODEL` | no | `gpt-4o` | |
| **Cloud — Claude model** | `CLAUDE_MODEL` | no | `claude-sonnet-4-20250514` | |
| **Cloud — OpenAI key** | `OPENAI_API_KEY` | yes | — | Edit via the GUI's secret store; the node only reports presence (`has_openai_key`) |
| **Cloud — Anthropic key** | `ANTHROPIC_API_KEY` | yes | — | Same |
| **Local model — path** | `LOCAL_MODEL_PATH` | yes | `models/SmolLM2-135M.Q2_K.gguf` | Path to a `.gguf` file |
| **Local model — n_ctx** | `LOCAL_N_CTX` | yes | `2048` | |
| **Local model — n_gpu_layers** | `LOCAL_N_GPU_LAYERS` | yes | `0` | `0` = pure CPU |
| **Local model — n_threads** | `LOCAL_N_THREADS` | yes | `12` | |
| **Local model — prefix words** | `PREFIX_WORD_COUNT` | no | `4` | Words local makes before cloud takes over |
| **MAS — pattern** | `MAS_PATTERN` | yes¹ | `sequential` | `sequential` / `moe` / `distill` / `deliberation` / `custom` |
| **MAS — rounds** | `MAS_ROUNDS` | yes¹ | `2` | Loop unroll count |
| **MAS — device** | `MAS_DEVICE` | yes¹ | `cpu` | Default device for HF agents |
| **MAS — dtype** | `MAS_DTYPE` | yes¹ | `float32` | Default dtype for HF agents |
| **MAS — agents** | `mas.json` | yes¹ | (canonical pair) | Edit via `PUT /node/config/mas` |
| **Storage — state dir** | `STATE_DIR` | yes | `.agent_state` | Where sessions + trained topics live |
| **Storage — idle flush** | `IDLE_FLUSH_SEC` | no | `180` | Seconds idle → flush + unload |
| **Storage — session TTL** | `SESSION_TTL_SEC` | no | `3600` | Seconds before evicting from RAM |
| **Storage — max context tokens** | `MAX_CONTEXT_TOKENS` | no | `6000` | Trigger compression at |

¹ MAS-shape changes can be applied without restarting the process by
calling `POST /node/mas/rebuild` after the patch.

**Per-call overrides** (no env var; pass on each `POST /node/mas/run`
or `/mas/stream`):

| GUI field | Body field | Default | Effect |
|---|---|---|---|
| Switch threshold | `switch_threshold` | `0.6` | Cosine sim below = candidate switch |
| Retrieval threshold | `retrieval_threshold` | `0.75` | Cosine sim above = reuse saved topic |
| Stage A steps | `stage1_steps` | `30` | Latent-alignment iterations |
| Stage B steps | `stage2_steps` | `20` | Token-CE iterations (0 disables) |
| Reformulations | `n_reformulations` | `6` | Question paraphrases asked from cloud |
| Max new tokens | `max_new_tokens` | `128` | Local generation length |
| Cloud continue | `cloud_continue` | `false` | Local prefix + cloud finish |
| Prefix tokens | `prefix_tokens` | `12` | Tokens of local prefix in continuator mode |
| Persist | `persist` | `true` | Save trained links to disk |
| Provider | `cloud_provider` | (default) | Per-session override of `DEFAULT_CLOUD` |
| Force cloud (per turn) | `force_cloud` | `false` | Skip local for this turn only |
| Force continue (per turn) | `force_continue` | `false` | Use continuator for this turn only |

---

## Error format

All non-stream errors return JSON:

```json
{"detail": "error message"}
```

Or:

```json
{"error": "missing bearer token"}
```

Status codes:
- `200` — success
- `400` — malformed body
- `401` — missing or wrong bearer token
- `404` — topic / session not found
- `500` — unhandled exception (the response body has the message)

Stream errors arrive as an SSE event:

```
data: {"event": "error", "type": "RuntimeError", "message": "…"}
```

Followed by a `done` event so the GUI can close the stream cleanly.

---

## Sample integration (TypeScript)

A minimal client that handles auth, info, and streaming MAS turns:

```ts
type NodeConfig = { host: string; port: number; key: string };

async function nodeFetch(cfg: NodeConfig, path: string, init: RequestInit = {}) {
  const res = await fetch(`http://${cfg.host}:${cfg.port}${path}`, {
    ...init,
    headers: {
      ...(init.headers || {}),
      "Authorization": `Bearer ${cfg.key}`,
      "Content-Type": "application/json",
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res;
}

export async function verifyKey(cfg: NodeConfig): Promise<boolean> {
  try {
    const r = await nodeFetch(cfg, "/node/auth/verify", { method: "POST" });
    return (await r.json()).ok === true;
  } catch { return false; }
}

export async function getInfo(cfg: NodeConfig) {
  return (await nodeFetch(cfg, "/node/info")).json();
}

export async function getConfig(cfg: NodeConfig) {
  return (await nodeFetch(cfg, "/node/config")).json();
}

export async function patchConfig(cfg: NodeConfig, patch: Record<string, any>) {
  const r = await nodeFetch(cfg, "/node/config", {
    method: "PATCH", body: JSON.stringify(patch),
  });
  return r.json();
}

export async function* runMasStream(cfg: NodeConfig, body: {
  message: string; session_id?: string; force_cloud?: boolean;
  force_continue?: boolean;
}) {
  const res = await nodeFetch(cfg, "/node/mas/stream", {
    method: "POST", body: JSON.stringify(body),
  });
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const p of parts) {
      if (!p.startsWith("data: ")) continue;
      yield JSON.parse(p.slice(6));
    }
  }
}

// Use:
// for await (const ev of runMasStream(cfg, { message: "..." })) {
//   if (ev.event === "stage1_step") updateProgressBar("A", ev.step, ev.total_steps);
//   if (ev.event === "answer")       displayAnswer(ev.text);
//   if (ev.event === "done")         closeStream();
// }
```

---

## Going from local-only to remote nodes

Today the node defaults to `--bind 127.0.0.1` (loopback only). For
Phase 2 (remote nodes connecting to a central server), the user runs:

```bash
python main.py node --bind 0.0.0.0 --port 9876 --auth-key $STABLE_KEY
```

Then the central server connects to `http://<user-public-ip>:9876` with
the shared key. The endpoint surface is identical — that's the whole
point of this module.

For production you'd want:
- TLS (terminate with caddy / nginx / cloudflared in front of the node)
- A more sophisticated auth scheme (JWT with expiry, refresh tokens) —
  this v1 uses a simple opaque bearer
- Per-route rate limiting (the existing `pressure.py` tracks but
  doesn't enforce; add a 429 in `/mas/run` if the user wants it)

But for an in-development GUI on the same machine, the current setup is
enough.

---

## Where to go next

- **[configuration.md](configuration.md)** — every knob explained for
  the user, useful for GUI copy
- **[training.md](training.md)** — the conceptual backing for the
  events you stream
- **[advanced_guide.md](advanced_guide.md)** — picking models +
  patterns
- **[main.md](main.md)** — code-level reference if you need to extend
  the node module
