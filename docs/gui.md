# GUI integration — wiring AGCL into a UI layer

<p align="left">
  <img src="assets/typescript.svg" width="22" alt="TypeScript" />&nbsp;
  <img src="assets/javascript.svg" width="22" alt="JavaScript" />&nbsp;
  <img src="assets/python.svg" width="22" alt="Python" />&nbsp;
  <img src="assets/fastapi.svg" width="22" alt="FastAPI" />&nbsp;
  <img src="assets/nodedotjs.svg" width="22" alt="Node.js" />&nbsp;
  <a href="https://img.shields.io/badge/transport-HTTP%20%2B%20SSE-purple"><img alt="Transport" src="https://img.shields.io/badge/transport-HTTP%20%2B%20SSE-purple"></a>
  <a href="https://img.shields.io/badge/auth-Bearer%20token-blue"><img alt="Auth" src="https://img.shields.io/badge/auth-Bearer%20token-blue"></a>
</p>

This doc is for whoever's writing the **GUI / web frontend** that talks
to a user's local AGCL (Agentic CLI) install. It's the front door — the
substantive content lives in the topic-specific files below.

> **Sister doc:** for plugging AGCL into agent platforms
> (MCP / Slack / Discord / Zapier) or for cloud / Docker / npm /
> Kubernetes targets, see **[integrations.md](integrations.md)**.

---

## Map of this guide

| Topic | What's there |
|---|---|
| **[connect.md](gui/connect.md)** | Auth pairing, ports, CORS posture, error format |
| **[endpoints.md](gui/endpoints.md)** | Every HTTP route the node exposes (auth, info, config, MAS, sessions, topics, chat) |
| **[sse-events.md](gui/sse-events.md)** | The full SSE event reference for `/node/mas/stream` |
| **[config-map.md](gui/config-map.md)** | Editable-config map: env vars, defaults, restart-required matrix |
| **[typescript-client.md](gui/typescript-client.md)** | Sample TypeScript client for `fetch` + SSE |
| **[remote.md](gui/remote.md)** | Going from local-only to remote nodes / production hardening |

If you're writing a GUI from scratch, read **connect → endpoints →
sse-events** in that order. The other three are reference material.

---

## Friendlier docs

| Doc | What it covers |
|---|---|
| **[guide.md](guide.md)** | Beginner setup |
| **[configuration.md](configuration.md)** | Every config knob in plain English |
| **[plugins.md](plugins.md)** | **Pluggable endpoints** — control recursive MAS (halt / pause / resume), inspect latent space, register your own routes via `plugins/` |
| **[minimodel.md](minimodel.md)** | The optional background mini-model trainer (toggleable, pausable, with attention presets and training strategies) |
| **[training.md](recursive/training.md)** | What auto-training does |
| **[recursive/setup.md](recursive/setup.md)** | Terse technical reference |
| **[main.md](code-map.md)** | Per-file code reference |

---

## What you're integrating with

```
┌─────────────────────────────────────────────────────┐
│              GUI / web frontend (yours)             │
└────────────────┬────────────────────────────────────┘
                 │  HTTP + SSE, Bearer auth
┌────────────────▼────────────────────────────────────┐
│  python main.py node --port 9876                    │
│  (FastAPI, same engine as the TUI)                  │
│                                                     │
│   /node/auth/verify       — pair the GUI            │
│   /node/info              — dashboard data          │
│   /node/config            — settings page           │
│   /node/mas/stream        — the live agent loop     │
│   /node/topics            — trained-state index     │
│   /node/plugins           — pluggable extensions    │
└─────────────────────────────────────────────────────┘
```

The TUI hits the same Python APIs in process, so every feature
described in these docs is also reachable from the shell as a slash
command.
