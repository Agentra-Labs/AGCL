# Web Console — using the bundled `/web_socket/` GUI

AGCL ships a static HTML/CSS/JS console at [`/web_socket/`](../web_socket/)
that drives every endpoint a local AGCL node exposes — chat, recursive
MAS, mini-trainer, usage / quotas, configuration, toolkit, plugins,
diagnostics, deploy manifests.

> **Sister docs:**
> - [docs/dashboard.md](dashboard.md) — the smaller `/node/dashboard`
>   page baked into the Python server (usage + quotas only)
> - [docs/gui.md](gui.md) — integration contract if you're building
>   your *own* GUI instead of using the bundled one

The console is dumb static files; all state lives on the AGCL node it
talks to. Your bearer key is stored only in browser `localStorage` and
never leaves your machine — every API call is a direct fetch to your
node host.

---

## TL;DR

```bash
# 1. start the node
python main.py node --bind 127.0.0.1 --port 9876 --cors "*"
# the node prints a one-time bearer key — copy it

# 2. serve the console (any of these works)
python -m http.server 8088 --directory .             # serve the repo root
# or just open index.html in a browser
# or push to GitHub Pages (see "Hosting" below) and visit your repo URL

# 3. visit http://localhost:8088/, paste the host + bearer key,
#    click Connect.
```

`index.html` lives at the repo root; everything else (`styles.css`,
`js/`, `assets/`) lives under `web_socket/`. Open the root entry point
and the relative paths inside it pull from `web_socket/`.

---

## Three ways to host the console

| Method                    | When to use                                                                 | URL                                                          |
|---------------------------|-----------------------------------------------------------------------------|--------------------------------------------------------------|
| **Open `index.html`**     | One-off, single user                                                        | `file://…/index.html`                                        |
| **Local static server**   | Local hacking, multiple browsers                                            | `python -m http.server 8088 --directory .`                   |
| **GitHub Pages**          | Sharing — the repo root becomes the published site that talks to *each visitor's own local node* | `https://<user>.github.io/<repo>/`                            |

GitHub Pages is fine because the console is purely client-side — there
is no server-side secret. Each visitor pastes the bearer key for
*their* local node; the page just acts as the rendering layer.

---

## Connecting

When you first open the console you get an auth pane:

| Field        | Default                       | Notes                                                  |
|--------------|-------------------------------|--------------------------------------------------------|
| Host         | `http://localhost:9876`       | Where the node is listening. Include scheme + port.   |
| Bearer key   | —                             | Printed once on `python main.py node` startup.        |

Click **Connect**. The console calls `POST /node/auth/verify` to
confirm the key works, then drops you into the Overview tab. Both
host and key are saved to `localStorage` for next time. **Disconnect**
in the sidebar clears them.

---

## Tabs (and which endpoints each one drives)

| Tab            | What you can do                                                                                       | Endpoints used                                                                                                                                                                                                                                                                              |
|----------------|--------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Overview       | System snapshot, MAS topology, local model, runtime info, GPUs                                         | `GET /node/info`, `GET /node/runtime/info`                                                                                                                                                                                                                                                   |
| Setup          | Auto-setup recursive MAS (autoconfig with optional model download). Auto-setup chat (.env defaults). HuggingFace snapshot downloader. Single GGUF file downloader. Recommended-models registry. Local cache inventory. Live job stream with progress bar + cancel. | `GET /node/setup/{registry,local-models,jobs,jobs/{id},jobs/{id}/stream}`, `POST /node/setup/{autoconfig,chat-quickstart,download/hf,download/gguf,jobs/{id}/cancel}`                                                                                                                       |
| Chat           | Streaming chat against any session id. Provider override, recovery mode toggles. Session list / flush. | `GET /node/sessions`, `GET/DELETE /node/sessions/{sid}`, `POST /node/chat/{sid}` (SSE)                                                                                                                                                                                                       |
| Recursive MAS  | Run a MAS turn (one-shot or streaming with training events). Pause / resume / halt / latent dump.      | `POST /node/mas/run`, `POST /node/mas/stream` (SSE), `GET/DELETE /node/mas/sessions[/{sid}]`, `POST /node/mas/sessions/{sid}/{pause,resume,halt}`, `GET /node/mas/sessions/{sid}/{status,latent}`, `POST /node/mas/rebuild`                                                                  |
| Topics         | List trained-state checkpoints, delete one (forces retrain on next visit)                              | `GET /node/topics`, `DELETE /node/topics/{id}`                                                                                                                                                                                                                                                |
| Mini-Trainer   | Status, start / stop / pause / resume, force-checkpoint, generate sample, edit config, view presets    | `GET/PATCH /node/mini/{status,config}`, `POST /node/mini/{start,stop,pause,resume,test,checkpoint}`, `GET /node/mini/presets`                                                                                                                                                                |
| Usage & Cost   | Per-provider summary, sessions table, timeseries chart, quota editor, custom OpenAI-compatible providers | `GET /node/usage/{summary,sessions,timeseries,quotas}`, `GET/POST/DELETE /node/usage/providers/custom[/{name}]`, `PUT/DELETE /node/usage/quota/{provider}`                                                                                                                                   |
| Configuration  | Curated config tree (cloud, local model, MAS, storage). Env editor with allowlist + persist toggles. mas.json upload. Bundle export / import. | `GET/PATCH /node/config`, `GET/PATCH /node/config/env`, `GET/PUT /node/config/mas`, `GET /node/config/all`, `POST /node/config/import`                                                                                                                                                       |
| Toolkit        | Adapter discovery / ping (LiteLLM / Ollama / vLLM / Redis / S3 / Cloudflare / WebRTC / GCP). Chat through gateway. Distributed state. Checkpoint store. | `GET /node/toolkit/{discover,checkpoints}`, `GET /node/toolkit/ping/{adapter}`, `POST /node/toolkit/chat`, `PUT/GET/DELETE /node/toolkit/{state,checkpoint}/...`                                                                                                                              |
| Plugins        | Discovered plugins / commands / tools, hot-reload                                                      | `GET /node/plugins`, `POST /node/plugins/reload`                                                                                                                                                                                                                                              |
| Diagnostics    | Pressure gauge, latency curve, active-hours bar, auto-refresh                                          | `GET /health`, `GET /pressure`, `GET /patterns`                                                                                                                                                                                                                                              |
| CLI Actions    | One-click flows that orchestrate existing endpoints: full snapshot, ping every adapter, cloud smoke test, strict-mode toggle, bundle export, flush-and-rebuild, remote node probe | All existing endpoints (no new server-side surface)                                                                                                                                                                                                                                          |
| Deploy         | Generate Docker / Kubernetes / GCP Cloud Run manifests                                                 | `POST /node/toolkit/manifest/{docker,k8s,gcp}`                                                                                                                                                                                                                                                |

Every toggle / button in the UI corresponds to one or more endpoints —
nothing is rendered client-side that wasn't fetched first.

---

## Mixed-content note (HTTPS site → HTTP localhost)

If you host on GitHub Pages (which forces HTTPS) and your node listens
on plain HTTP `localhost`, the browser will treat `http://localhost`
as **mixed content**. In practice:

| Browser   | Behavior                                                                                                  |
|-----------|------------------------------------------------------------------------------------------------------------|
| Chrome    | Allows it. `http://localhost` is a "potentially trustworthy" origin. Just works.                            |
| Edge      | Allows it. Same rationale as Chrome.                                                                        |
| Firefox   | Stricter. May block. Workarounds below.                                                                    |
| Safari    | Allows it for `localhost` in recent versions.                                                               |

**Workarounds** (only needed if your browser blocks the connection):

1. **Easiest** — serve the console locally too:
   ```bash
   python -m http.server 8088 --directory web_socket
   ```
   Then visit `http://localhost:8088/`. Origin and target are both
   HTTP, no mixed content.

2. **Production** — put the node behind a TLS reverse proxy (Caddy,
   nginx + Let's Encrypt) and use the HTTPS URL in the auth pane:
   ```
   https://agcl.your-domain.tld
   ```

3. **Firefox testing only** — `about:config` →
   `security.mixed_content.block_active_content` → `false`. Not
   recommended permanently.

---

## CORS

The node defaults to `--cors "*"` which lets any origin connect. For
production, tighten this:

```bash
# only allow your GitHub Pages site
python main.py node --port 9876 --cors "https://your-user.github.io"

# multiple origins
python main.py node --port 9876 --cors "https://a.example.com,https://b.example.com"
```

The console itself doesn't care — whatever origin you serve it from
just needs to be in the node's CORS allowlist.

---

## File layout

```
index.html                # entry point — lives at repo root
web_socket/
  styles.css              # monochrome dark/light theme, monospace
  README.md               # short hosting note (most info is here)
  assets/
    icon.png              # local copy of the repo icon
    icons/                # additional bundled SVGs (currently empty)
  js/
    api.js                # fetch + SSE wrapper, bearer auth, localStorage
    app.js                # router, sidebar nav, connection panel, toast
    lib/
      markdown.js         # minimal Markdown renderer (no deps)
      charts.js           # SVG charts: line, bar, sparkline, donut, heatmap, gauge
    views/
      overview.js         # system info, MAS, local model, GPUs, token donut
      chat.js             # streaming chat with Markdown rendering
      mas.js              # recursive MAS — run, stream, control, latent heatmap, loss curve
      topics.js           # trained-state checkpoints
      mini.js             # mini-model trainer with live loss + throughput curves
      usage.js            # tokens, cost, quotas, custom providers, donut + bar/line charts
      config.js           # curated config, env editor, mas.json, bundles
      toolkit.js          # adapter discover/ping, chat gateway with Markdown, state
      plugins.js          # discovered plugins / commands / tools
      diagnostics.js      # pressure gauge, latency line, active-hours bar
      actions.js          # CLI-parity one-click flows: snapshot, ping-all, smoke test, strict toggle
      deploy.js           # emit Docker / k8s / GCP manifests
```

No build step. No npm. No bundler. Open in any modern browser and it
runs.

---

## Adding a new tab

The console is intentionally easy to extend. To add a new tab:

1. Create `web_socket/js/views/<name>.js`. Register on
   `window.Views.<name> = { mount(root) { ... return view; } }`.
2. Add the script tag to `web_socket/index.html` (above `app.js`).
3. Add a sidebar link to `<nav class="nav" id="nav">` with
   `data-view="<name>"` and an `<svg>` icon (define a new
   `<symbol id="i-<name>">` in the sprite at the top of `index.html`).
4. Add `<name>: "Display Title"` to `VIEW_TITLES` in
   [`js/app.js`](../web_socket/js/app.js).

The view's `mount(root)` should return an object with optional
`unmount()` (cleanup, abort streams, clear timers) and `refresh()`
(re-fetch when the topbar Refresh button is clicked).

The shared API client is `window.API` (see [`js/api.js`](../web_socket/js/api.js)):

```js
await API.get("/node/info");
await API.post("/node/mas/run", body);
const stream = API.sse("/node/mas/stream", body, {
  onEvent(ev) { ... },
  onError(e)  { ... },
  onDone()    { ... },
});
stream.abort();   // cancel mid-stream
```

Toasts and helpers come from `window.UI`:

```js
UI.ok("Done!");
UI.err(new Error("Something broke"));
UI.toast("Heads-up", "warn");
```

---

## Troubleshooting

| Symptom                                              | Cause / fix                                                                                  |
|-------------------------------------------------------|----------------------------------------------------------------------------------------------|
| "Connect failed: Network error"                       | Node not running, or wrong host/port. Run `curl http://localhost:9876/node/health`.          |
| "Saved key rejected"                                  | Node was restarted (key is generated per startup unless you set `AGCL_NODE_AUTH`).            |
| Auth pane reappears after refresh                     | Browser cleared `localStorage` (private mode?). Re-paste.                                     |
| 401 / 403 on every request                            | CORS allowed but bearer key wrong. Logout → reconnect with the key the node currently prints. |
| CORS error in DevTools                                | Node's `--cors` allowlist doesn't include your origin. Restart with `--cors "*"` or your URL. |
| Mixed-content blocked (Firefox, GitHub Pages → HTTP)  | See *Mixed-content note* above.                                                              |
| Tab is empty / spinner forever                        | Open DevTools → Network. Look for the failing fetch. Most likely the node is missing the route (older AGCL build). |
| Streaming chat hangs                                   | Provider key not set or quota hit. Check Usage & Cost → quotas.                              |
| Mini-trainer status shows `enabled: no` and won't start| Set `MINI_ENABLED=1` (Configuration tab → env vars), then click Start.                       |

---

## Persistent settings

The console stores three things in `localStorage` (per origin):

| Key              | What it holds                                              |
|------------------|-------------------------------------------------------------|
| `agcl.host`      | Node URL you connected to                                   |
| `agcl.key`       | Bearer key                                                  |
| `agcl.chat.sid`  | Last chat session id (Chat tab)                             |
| `agcl.mas.sid`   | Last selected MAS session id (Recursive MAS tab)            |

Clearing them: click **Disconnect** in the sidebar, or use DevTools →
Application → Local Storage → clear.
