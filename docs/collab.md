# AGCL Collaboration Platform

AGCL Collab turns your AGCL node into a full-fledged collaboration platform where **humans and AI agents participate as peers** in fluid, on-demand **spaces**.

## Architecture

```
web/                    Vue 3 + Vite SPA (browser client)
relay/                  Optional cloud relay (FastAPI + WebSocket)
agcl/collab/            Backend layer (mounted on existing node)
  spaces.py             Space CRUD, membership, invite codes
  messages.py           Message store, @mention parsing
  tasks.py              Task lifecycle (promote, assign, kanban)
  presence.py           Online/typing indicators + ambient intelligence
  agents.py             Agent registry and peer dispatch
  relay_client.py       Optional relay bridge
  router.py             /node/collab/* REST + SSE routes
  cli.py                `agcl collab` subcommand
```

All new routes live under `/node/collab/*` and use the same bearer-auth gate as the rest of the node. **No existing routes are modified.**

## Quick Start

### 1. Start the node

```bash
python main.py node --port 9876 --cors "*"
```

### 2. Start the web frontend

```bash
cd web
npm install
npm run dev
# open http://localhost:5173
```

### 3. Connect and create a space

Open the browser, paste your node URL + bearer key, then click **+ New Space**.

Share the invite code with teammates: they click **Join via code** and enter the space ID + code.

### 4. Add an agent

In the space, agents from your plugin registry and MAS are available. Add one via the API or CLI:

```bash
agcl collab agents list
# then add to a space via the web UI or:
curl -X POST http://localhost:9876/node/collab/spaces/<id>/agents \
  -H "Authorization: Bearer <key>" \
  -d '{"agent_id": "multica"}'
```

### 5. @mention an agent

Type `@agent-name` in the composer — the agent responds inline as a peer.

### 6. Promote a message to a task

Hover any message → click **📌 Task** → assign to a human or agent → watch the kanban board.

## CLI Reference

```bash
agcl collab create "My Space"          # create space, prints invite code
agcl collab join <space_id> <code>     # join a space
agcl collab send <space_id> "message"  # post a message headlessly
agcl collab spaces                     # list spaces
agcl collab agents list                # list available agents
agcl collab tasks list <space_id>      # list tasks
```

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| POST | `/node/collab/spaces` | Create space |
| GET | `/node/collab/spaces` | List spaces |
| GET | `/node/collab/spaces/{id}` | Space detail + members |
| POST | `/node/collab/spaces/{id}/join` | Join via invite code |
| GET | `/node/collab/spaces/{id}/messages` | Paginated message history |
| POST | `/node/collab/spaces/{id}/messages` | Post message |
| GET | `/node/collab/spaces/{id}/stream` | SSE stream (messages + presence + task updates) |
| GET | `/node/collab/agents` | List available agents |
| POST | `/node/collab/spaces/{id}/agents` | Add agent to space |
| DELETE | `/node/collab/spaces/{id}/agents/{agent_id}` | Remove agent |
| POST | `/node/collab/spaces/{id}/tasks` | Create task |
| GET | `/node/collab/spaces/{id}/tasks` | List tasks |
| PATCH | `/node/collab/tasks/{task_id}` | Update task |
| POST | `/node/collab/spaces/{id}/presence` | Heartbeat |
| POST | `/node/collab/spaces/{id}/typing` | Typing indicator |
| PATCH | `/node/collab/spaces/{id}/ambient` | Configure ambient intelligence |

## SSE Event Shape

All events on `/node/collab/spaces/{id}/stream` follow:

```json
{"type": "message|presence|task_update", "data": {...}}
```

## Ambient Intelligence

Enable per-space ambient mode to have an agent silently observe conversations and surface suggestions:

```bash
curl -X PATCH http://localhost:9876/node/collab/spaces/<id>/ambient \
  -H "Authorization: Bearer <key>" \
  -d '{"ambient_enabled": true, "ambient_agent_id": "my-agent", "ambient_trigger_every_n": 5}'
```

After every 5 messages the agent is invoked with the last 10 messages as context. Suggestions appear with a 💡 badge in the UI.
