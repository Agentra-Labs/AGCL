# AGCL Relay Service

The relay is an optional FastAPI + WebSocket service that enables:

1. **Remote access** — teammates reach your self-hosted AGCL node without port-forwarding
2. **Async message buffering** — messages are buffered for offline nodes/users (last 500 per space)
3. **Nodeless spaces** — teams without a local AGCL node can use the relay as the backend

## Quick Start

```bash
cd relay
pip install -r requirements.txt
uvicorn relay.main:app --port 8765
```

## AGCL Node → Relay Bridge

Set `AGCL_RELAY_URL` before starting the node:

```bash
export AGCL_RELAY_URL=wss://your-relay.example.com
python main.py node --port 9876
```

The node auto-connects to the relay on startup and bridges collab events bidirectionally.

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/relay/register` | Register a node; returns `relay_token` |
| POST | `/relay/spaces` | Create a relay-hosted (nodeless) space |
| GET | `/relay/invite/{code}` | Resolve invite code → space + node URL |
| GET | `/relay/health` | Liveness check |
| WS | `/relay/node/{node_id}` | Node WebSocket connection |
| WS | `/relay/client/{space_id}` | Browser client WebSocket (nodeless mode) |

## Node WebSocket Protocol

After connecting to `WS /relay/node/{node_id}`, send a registration message:

```json
{"type": "register", "space_ids": ["space-id-1", "space-id-2"]}
```

The relay delivers buffered messages for those spaces, then forwards all subsequent events to other nodes/clients in the same spaces.

To forward a collab event:

```json
{"type": "message", "space_id": "space-id-1", "data": {...}}
```

## Nodeless Mode

Teams without a local AGCL node can create a relay-hosted space:

```bash
curl -X POST https://your-relay.example.com/relay/spaces \
  -d '{"name": "my-team"}'
# returns: {"space_id": "...", "invite_code": "..."}
```

Browser clients connect via `WS /relay/client/{space_id}` and receive the full message history on connect.

## Deployment

### Docker

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY relay/ relay/
RUN pip install -r relay/requirements.txt
CMD ["uvicorn", "relay.main:app", "--host", "0.0.0.0", "--port", "8765"]
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8765` | Port to listen on |
| `RELAY_DB_PATH` | `relay_state.db` | SQLite database path |

## Security Notes

- The relay does not authenticate individual messages — it trusts nodes that have registered with a valid `relay_token`
- For production, put the relay behind a TLS reverse proxy (nginx, Caddy, Cloudflare Tunnel)
- Rotate relay tokens periodically via `POST /relay/register`
