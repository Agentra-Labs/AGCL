"""
relay.main — AGCL cloud relay service.

Brokers WebSocket connections between AGCL nodes and browser clients,
buffers messages for offline nodes, and hosts spaces for nodeless teams.

Run: uvicorn relay.main:app --port 8765
"""
from __future__ import annotations

import json
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from relay import broker, space_store

app = FastAPI(title="AGCL Relay")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup():
    space_store.init_db()


# ── REST ──────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    node_id: str
    space_ids: List[str] = []


class CreateSpaceBody(BaseModel):
    name: str
    node_url: str = ""


@app.post("/relay/register")
def register(body: RegisterBody):
    token = space_store.register_node(body.node_id, body.space_ids)
    return {"relay_token": token, "node_id": body.node_id}


@app.post("/relay/spaces")
def create_space(body: CreateSpaceBody):
    return space_store.create_space(body.name, body.node_url)


@app.get("/relay/invite/{code}")
def resolve_invite(code: str):
    result = space_store.resolve_invite(code)
    if not result:
        raise HTTPException(404, "invite code not found")
    return {
        "space_id": result["id"],
        "relay_url": "",   # filled by client from request origin
        "node_url": result.get("node_url", ""),
        "invite_code": code,
    }


@app.get("/relay/health")
def health():
    return {"ok": True}


# ── WebSocket: node ───────────────────────────────────────────────────

@app.websocket("/relay/node/{node_id}")
async def node_ws(websocket: WebSocket, node_id: str):
    await websocket.accept()
    space_ids: List[str] = []
    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)

            if event.get("type") == "register":
                space_ids = event.get("space_ids", [])
                for sid in space_ids:
                    broker.register_node(sid, websocket)
                    # deliver buffered messages
                    for msg in space_store.get_buffered(sid):
                        await websocket.send_text(json.dumps(msg))
                continue

            space_id = event.get("space_id")
            if space_id:
                space_store.buffer_message(space_id, event)
                await broker.broadcast_to_space(space_id, event, exclude=websocket)

    except WebSocketDisconnect:
        pass
    finally:
        for sid in space_ids:
            broker.unregister_node(sid, websocket)


# ── WebSocket: browser client ─────────────────────────────────────────

@app.websocket("/relay/client/{space_id}")
async def client_ws(websocket: WebSocket, space_id: str):
    await websocket.accept()
    broker.register_client(space_id, websocket)
    # deliver recent history
    for msg in space_store.get_buffered(space_id, limit=100):
        await websocket.send_text(json.dumps(msg))
    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)
            event["space_id"] = space_id
            space_store.buffer_message(space_id, event)
            await broker.broadcast_to_space(space_id, event, exclude=websocket)
    except WebSocketDisconnect:
        pass
    finally:
        broker.unregister_client(space_id, websocket)
