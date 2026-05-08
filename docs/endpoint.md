# AGCL pluggable endpoints

This document is the reference for the **pluggable control surface** of
AGCL (Agentic CLI). Two things live here:

1. **Built-in control endpoints** for the recursive MAS engine: halt,
   pause, resume training; inspect the latent space mid-run; recursive
   mass-runner usage; status snapshots.
2. **The plugin contract** - how to drop a `.py` file in `plugins/`
   that registers your own HTTP routes and TUI slash commands.

Read this alongside [integration.md](integration.md), which covers the
auth flow, CORS posture, and the existing endpoint surface.

---

## When to read this doc

- You want to **halt training** because the loss curve has plateaued.
- You want to **pause** mid-run, snapshot the latent, then resume.
- You're building a tool that needs to **call the recursive MAS in
  bulk** (mass-runner pattern).
- You want to add **your own endpoints** without forking AGCL.

---

## Quick map

| What you want | How to do it |
|---|---|
| Halt training mid-run | `POST /node/mas/sessions/{sid}/halt` |
| Pause / resume | `POST /node/mas/sessions/{sid}/pause` then `/resume` |
| Inspect latent space | `GET /node/mas/sessions/{sid}/latent` |
| Bulk-run many prompts | Loop `POST /node/mas/run` (see "Recursive mass" below) |
| Add your own route | Drop a plugin in `plugins/` |
| Add a TUI command | `ctx.add_command("name", help="...")` in your plugin |
| List loaded plugins | `GET /node/plugins` (HTTP) or pick `plugins` in the TUI menu |

---

## 1. Training control endpoints

All four mutate a `TrainingControl` handle attached to the named MAS
session. The auto-training loop polls the handle once per step, so
control requests take effect at the next step boundary - never inside
a backward pass.

The session must already exist (i.e. you've called `/node/mas/run` or
`/node/mas/stream` for that `session_id` at least once).

### `POST /node/mas/sessions/{sid}/pause`

Block the training loop at its next step boundary. Generation outside
of training is unaffected.

```http
POST /node/mas/sessions/default/pause
Authorization: Bearer ...
```

```json
{
  "ok": true,
  "status": {
    "paused":  true,
    "halted":  false,
    "stage":   "stage1",
    "step":    7,
    "latent_shape": [1, 896],
    "has_latent":   true
  }
}
```

The corresponding SSE event arriving on the open `/node/mas/stream`
connection is:

```
data: {"event": "paused", "stage": "stage1", "step": 7}
```

### `POST /node/mas/sessions/{sid}/resume`

Unblock a paused loop. The SSE stream emits `{"event": "resumed", ...}`.

### `POST /node/mas/sessions/{sid}/halt`

Stop training. The loop raises `HaltedError` at its next step
boundary, which the runner catches; the SSE stream emits

```
data: {"event": "halted", "stage": "stage2", "step": 4}
data: {"event": "halted_done", "message": "halted during stage2 step 4"}
data: {"event": "done"}
```

Halt is non-recoverable for *this* turn. Subsequent turns on the same
session reset the control state automatically.

### `GET /node/mas/sessions/{sid}/status`

Cheap polling endpoint. Same payload shape as the `status` object
inside `pause`/`resume`/`halt` responses.

### `GET /node/mas/sessions/{sid}/latent`

Snapshot the most recently observed loop latent vector. Useful for
inspecting how the latent space evolves during cloud-teacher training.

```json
{
  "ok": true,
  "snapshot": {
    "shape":  [1, 896],
    "stage":  "stage1",
    "step":   12,
    "values": [0.0123, -0.0456, ...]
  }
}
```

The values list is capped at 4096 floats so a giant tensor can't bloat
the response. `null` snapshot means no step has produced a latent yet
(e.g. the session is fresh).

### Idempotency + race handling

- Pausing an already-paused loop is a no-op.
- Halting an already-halted session is a no-op (subsequent turns auto-
  reset).
- The control object is per-session, so halting session A does not
  affect session B even if they share the underlying MAS singleton.
- Status reflects the *most recent observed* values; there's a
  one-step lag while the loop is in flight.

---

## 2. Recursive mass usage

"Mass" here means **driving the recursive MAS over a batch of related
prompts** - reformulations, sweeps, evaluation runs - using the same
node API. There is no dedicated endpoint; the pattern is:

1. Pick a `session_id` (re-using one preserves trained-link state).
2. For each prompt, `POST /node/mas/run` (blocking) or `/mas/stream`
   (event-by-event).
3. Between prompts, optionally:
   - `POST /node/mas/sessions/{sid}/halt` to abort a runaway prompt
   - `GET  /node/mas/sessions/{sid}/latent` to checkpoint latents
   - `POST /node/mas/rebuild` to force a clean state

```python
import httpx
H = {"Authorization": "Bearer KEY"}
prompts = ["explain X", "now Y", "compare X and Y", ...]
for p in prompts:
    r = httpx.post("http://localhost:9876/node/mas/run",
                   headers=H, json={"session_id": "sweep", "message": p},
                   timeout=600).json()
    print(r["topic_id"], r["answer"][:80])
```

For very long sweeps, use `/mas/stream` and parse `stage1_step` events
to monitor training progress in real time. If a sweep is misbehaving,
fire halt at the per-session endpoint - the in-flight turn aborts at
its next step boundary, but **the session itself stays alive** for the
next prompt.

If you need parallel mass-runs against the same model, use distinct
`session_id` values; the MAS singleton is shared but each session has
its own RecursiveSession + control handle.

---

## 3. Plugin contract

A plugin is a single Python file at `plugins/<name>.py`. Every plugin
exposes a `register(ctx)` function which AGCL calls once at startup -
both when the node server boots and when the TUI launches.

### Minimal plugin

```python
# plugins/hello.py
def register(ctx):

    @ctx.router.get("/hello")
    def hello():
        return {"hello": "world"}

    @ctx.add_command("hello", help="say hi from a plugin")
    def _cmd_hello(args: str):
        target = args.strip() or "world"
        print(f"hello, {target}!")
```

After dropping this file in `plugins/`, restart AGCL. The HTTP route
mounts at `/node/hello` (every plugin route is namespaced under
`/node/`). In the TUI, `/hello` becomes a recognized slash command -
the linter stops flagging it as unknown.

### `ctx` API

```
ctx.router                 # FastAPI APIRouter prefixed with /node
ctx.add_route(path, fn,    # imperative alternative to decorators
              method="GET")
ctx.add_command(name, ...) # decorator: register a TUI slash command
ctx.get_mas()              # the live RecursiveMAS singleton
ctx.sessions()             # dict of {session_id: RecursiveSession}
ctx.get_control(session_id)# the per-session TrainingControl handle
```

### What you can do

- Add HTTP routes for custom telemetry, logging, exports, etc.
- Add slash commands for the TUI (e.g. `/dump-latent`, `/export-topic`).
- Reach into `ctx.get_control(sid)` to halt/pause programmatically -
  the same handle the built-in endpoints mutate, so plugin halts
  cooperate cleanly with GUI halts.
- Read the MAS via `ctx.get_mas()` to inspect link weights, agent
  configs, dimensions, etc.

### What you can't do

- Mutate already-registered routes (they're frozen by FastAPI once
  the router is included).
- Block startup - if your `register()` raises, AGCL records the error
  in `/node/plugins` and continues loading the rest. Your plugin's
  routes simply won't appear.
- Sandbox themselves: plugins run with the same privileges as the host
  process. Only load plugins you trust.

### Plugin discovery

```
plugins/
    hello.py           # picked up
    _internal.py       # ignored (leading underscore)
    not_a_plugin.txt   # ignored (not .py)
    sub/
        nested.py      # ignored (only top level)
```

Override the directory with `AGCL_PLUGINS_DIR=/path/to/dir`.

### Inspecting load results

```http
GET /node/plugins
```

```json
{
  "plugins": [
    {"name": "hello", "path": "plugins/hello.py", "ok": true,  "doc": ["Demo plugin."]},
    {"name": "broken","path": "plugins/broken.py","ok": false, "error": "ImportError: ..."}
  ]
}
```

In the TUI, the `plugins` menu entry shows the same data plus all
registered slash commands.

---

## 4. SSE event additions

In addition to the events documented in
[integration.md](integration.md#event-reference), the streaming
endpoint may emit:

| `event` | Fields | When |
|---|---|---|
| `paused` | `stage`, `step` | training loop blocked by an external `/pause` |
| `resumed` | `stage`, `step` | unblocked by `/resume` |
| `halted` | `stage`, `step` | external `/halt`; loop is about to raise |
| `halted_done` | `message` | runner caught the halt; `done` follows |

A halted stream still terminates with `{"event": "done"}` so client
code can use the same close-on-`done` pattern.

---

## 5. End-to-end example: halt + inspect from a notebook

```python
import httpx, time, json
H = {"Authorization": "Bearer KEY"}
BASE = "http://localhost:9876"

# kick off a streaming turn in a thread
def stream():
    with httpx.stream("POST", f"{BASE}/node/mas/stream",
                      headers=H, json={"session_id": "demo",
                                        "message": "explain entropy"},
                      timeout=None) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                print(ev.get("event"), ev)
                if ev.get("event") == "done":
                    break

import threading
t = threading.Thread(target=stream, daemon=True); t.start()
time.sleep(8)                                       # let stage A run a bit

# pause, snapshot the latent, halt
httpx.post(f"{BASE}/node/mas/sessions/demo/pause", headers=H).json()
snap = httpx.get(f"{BASE}/node/mas/sessions/demo/latent", headers=H).json()
print("latent shape:", snap["snapshot"]["shape"])
httpx.post(f"{BASE}/node/mas/sessions/demo/halt", headers=H).json()
t.join()
```

---

## 7. Mini-model control endpoints

The optional background mini-trainer (see
[minimodel.md](minimodel.md)) exposes its own slice of `/node/mini/*`
routes. All require auth.

| Endpoint | Effect |
|---|---|
| `GET  /node/mini/status`     | Step count, last loss, buffer fill, paused/running flags |
| `GET  /node/mini/config`     | Current `MiniConfig` dataclass as JSON |
| `PATCH /node/mini/config`    | Partial update; auto-rebuilds if arch-shaping fields change |
| `POST /node/mini/start`      | Enable + spin up the background thread |
| `POST /node/mini/stop`       | Halt the trainer (joins thread within 5s) |
| `POST /node/mini/pause`      | Suspend (resumable) |
| `POST /node/mini/resume`     | Resume after pause |
| `POST /node/mini/test`       | Body: `{prompt, max_new?, temperature?}` -> generate |
| `POST /node/mini/checkpoint` | Force save to `MINI_STATE_DIR/model.pt` |
| `GET  /node/mini/presets`    | Strategies, attention presets, archs |

Status payload:

```json
{
  "enabled":      true,
  "running":      true,
  "paused":       false,
  "step":         137,
  "last_loss":    0.482,
  "loss_recent":  [0.49, 0.485, 0.482, ...],
  "samples_seen": 412,
  "buffer":       16,
  "buffer_max":   1024,
  "arch":         "transformer",
  "strategy":     "ce_plus_latent",
  "attention":    "causal",
  "started_at":   1715300000.0,
  "error":        null
}
```

PATCH example - swap to a sliding-window attention with smaller LR:

```http
PATCH /node/mini/config
Authorization: Bearer KEY
Content-Type: application/json

{"attention": "sliding", "window": 32, "lr": 1e-4}
```

```json
{
  "ok": true,
  "applied": ["attention", "window", "lr"],
  "requires_rebuild": true
}
```

`requires_rebuild: true` means the trainer just got torn down and
re-instantiated under the hood; you don't need to do anything.

---

## 8. CLI parity in the TUI

Every control endpoint has a matching slash command in the persistent
TUI shell:

| HTTP | TUI |
|---|---|
| `/pause` | `/pause` |
| `/resume` | `/resume` |
| `/halt` | `/halt` |
| `/status` | `/status` |
| `/latent` | `/latent` |
| `/node/plugins` | `plugins` from the main menu |
| `/node/mini/status` | `/mini-status` (or `minimodel` menu) |
| `/node/mini/start` | `/mini-start` |
| `/node/mini/stop` | `/mini-stop` |
| `/node/mini/pause` | `/mini-pause` |
| `/node/mini/resume` | `/mini-resume` |
| `/node/mini/test` | `/mini-test <prompt>` |

The linter recognizes all of them; typos surface a "did you mean ...?"
hint instead of a silent failure.
