# Connect — auth, ports, and CORS

How a user pairs their PC with your GUI / web frontend, and the security
posture behind it.

> Part of the **[GUI integration guide](../gui.md)**.
> See also: [Endpoints](endpoints.md) · [SSE events](sse-events.md) ·
> [Config map](config-map.md) · [TypeScript client](typescript-client.md) ·
> [Remote nodes](remote.md)

---

## Two ways to drive AGCL

| Mode | What it is | When to use |
|---|---|---|
| **TUI shell** (default) | `python main.py` opens a sustained, arrow-key-navigable terminal app with linted slash commands | End users on a single PC; no HTTP needed |
| **Node server** (opt-in) | `python main.py node --port 9876` exposes the same engine over HTTP+SSE with bearer auth | A separate GUI talking to a user's PC |

The server is a feature, **not the default startup path**. Everything in
this guide targets the node-server mode since that's what a GUI
integrates with. The TUI hits the same Python APIs in process, so every
feature is also reachable from the shell as a slash command.

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

Build URLs as `http://<host>:<port>` + the paths in
[endpoints.md](endpoints.md).

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

## Error format

All non-stream errors return JSON:

```json
{"detail": "error message"}
```

Or, for missing auth:

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
