# AGCL Console — web GUI

A static HTML/CSS/JS site that drives a locally-running AGCL node.
Hostable on GitHub Pages (or any static file host) and used as the
GUI layer for everything the CLI / TUI exposes — chat, recursive MAS,
mini-trainer, usage / quotas, configuration, toolkit, plugins,
diagnostics, deploy manifests.

The site itself is dumb static files; all state lives on the AGCL
node it talks to. Your bearer key lives only in browser localStorage
and never leaves your machine — every API call is a direct fetch to
your node host.

## Quick start

1. Start a node on the host machine:

   ```
   python main.py node --bind 127.0.0.1 --port 9876 --cors "*"
   ```

   The node prints a one-time bearer key. Copy it.

2. Open the site:

   - **Local file**: open `index.html` directly in a browser.
   - **GitHub Pages**: push the repo, enable Pages on `main` /
     root, then visit `https://<user>.github.io/<repo>/web_socket/`.
   - **Local static server** (recommended for local hacking):

     ```
     python -m http.server 8088 --directory web_socket
     ```

     Visit `http://localhost:8088/`.

3. In the auth pane, paste the host (default
   `http://localhost:9876`) and the bearer key. Click Connect.

## Mixed-content note (GitHub Pages → localhost)

GitHub Pages forces HTTPS. Calling `http://localhost:9876` from an
`https://*.github.io` origin is **mixed content** — Chrome allows
this for `localhost` (it is a "potentially trustworthy" origin),
but Firefox is stricter. If the connection fails:

- **Easiest**: serve the site locally
  (`python -m http.server` from `/web_socket`) so origin and
  target are both HTTP.
- **Or**: run the node behind a reverse proxy with TLS
  (Caddy / nginx + Let's Encrypt) and put the HTTPS URL in the
  auth pane.
- **Or (Firefox only)**: visit `about:config`, set
  `security.mixed_content.block_active_content = false` for
  testing — not recommended permanently.

## CORS

The node CLI defaults to `--cors "*"` which lets any origin connect.
Tighten this in production by passing a comma-separated list:

```
python main.py node --port 9876 --cors "https://your.github.io"
```

## File layout

```
web_socket/
  index.html              # shell + SVG icon sprite
  styles.css              # monochrome dark/light theme
  README.md               # you are here
  assets/
    icon.png              # local copy of repo icon
  js/
    api.js                # fetch + SSE wrapper, bearer auth, localStorage
    app.js                # router, nav, connection panel, toast
    views/
      overview.js         # system info, MAS, local model, GPUs
      chat.js             # streaming chat (/node/chat/{sid})
      mas.js              # recursive MAS run + stream + pause/resume/halt
      topics.js           # trained-state topic checkpoints
      mini.js             # background mini-model trainer
      usage.js            # tokens, cost, quotas, custom providers
      config.js           # curated config, env editor, mas.json, bundles
      toolkit.js          # adapter discover/ping, chat gateway, state
      plugins.js          # discovered plugins / commands / tools
      diagnostics.js      # health, pressure, active-hours patterns
      deploy.js           # emit Docker / k8s / GCP manifests
```

## What each tab maps to

| Tab            | Endpoints used                                                         |
| -------------- | ---------------------------------------------------------------------- |
| Overview       | `GET /node/info`, `GET /node/runtime/info`                             |
| Chat           | `GET/DEL /node/sessions[/{sid}]`, `POST /node/chat/{sid}` (SSE)        |
| Recursive MAS  | `GET/DEL /node/mas/sessions[/{sid}]`, `POST /node/mas/run`, `POST /node/mas/stream` (SSE), `POST /node/mas/sessions/{sid}/{pause\|resume\|halt}`, `GET /node/mas/sessions/{sid}/{status\|latent}`, `POST /node/mas/rebuild` |
| Topics         | `GET /node/topics`, `DEL /node/topics/{id}`                            |
| Mini-Trainer   | `GET/PATCH /node/mini/{status,config}`, `POST /node/mini/{start,stop,pause,resume,test,checkpoint}`, `GET /node/mini/presets` |
| Usage & Cost   | `GET /node/usage/{summary,sessions,timeseries,quotas,providers/custom}`, `PUT/DEL /node/usage/quota/{provider}`, `POST/DEL /node/usage/providers/custom[/{name}]` |
| Configuration  | `GET/PATCH /node/config`, `GET/PATCH /node/config/env`, `GET/PUT /node/config/mas`, `GET /node/config/all`, `POST /node/config/import` |
| Toolkit        | `GET /node/toolkit/{discover,checkpoints}`, `GET /node/toolkit/ping/{adapter}`, `POST /node/toolkit/chat`, `PUT/GET/DEL /node/toolkit/{state,checkpoint}/...` |
| Plugins        | `GET /node/plugins`, `POST /node/plugins/reload`                       |
| Diagnostics    | `GET /health`, `GET /pressure`, `GET /patterns`                        |
| Deploy         | `POST /node/toolkit/manifest/{docker,k8s,gcp}`                         |

## Assets

`assets/icon.png` is a local copy of the repo's `icon.png`. The site
otherwise uses inline SVG (no emoji, no external icon CDN). If you
add new icons, drop them in `assets/icons/` and reference them from
the SVG sprite in `index.html`.

## Hosting on GitHub Pages

1. Settings → Pages → Source: `Deploy from a branch` → branch
   `main`, folder `/ (root)`.
2. Wait for the build, then visit
   `https://<user>.github.io/<repo>/web_socket/`.
3. Configure your local node with `--cors "https://<user>.github.io"`
   for tighter security than the wildcard default.
