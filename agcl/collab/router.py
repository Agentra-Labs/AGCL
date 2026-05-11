"""
agcl.collab.router — /node/collab/* REST + SSE routes.

Mounted in build_node_app() via:
    from agcl.collab.router import make_collab_router
    base_app.include_router(make_collab_router())

SSE stream per space uses an in-process asyncio.Queue fan-out.
All existing /node/* routes are untouched.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agcl.collab import spaces as S

# ── per-space SSE fan-out ─────────────────────────────────────────────
# space_id → list of asyncio.Queue
_streams: Dict[str, List[asyncio.Queue]] = {}


def _get_queues(space_id: str) -> List[asyncio.Queue]:
    return _streams.setdefault(space_id, [])


async def broadcast(space_id: str, event: Dict[str, Any]) -> None:
    payload = f"data: {json.dumps(event)}\n\n"
    for q in list(_get_queues(space_id)):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ── request bodies ────────────────────────────────────────────────────

class CreateSpaceBody(BaseModel):
    name: str
    description: str = ""
    owner_id: str = "owner"


class JoinSpaceBody(BaseModel):
    invite_code: str
    user_id: str = "guest"
    display_name: str = "Guest"


class PostMessageBody(BaseModel):
    sender_id: str
    sender_type: str = "human"
    content: str
    thread_id: Optional[str] = None


class AmbientConfigBody(BaseModel):
    ambient_enabled: bool
    ambient_agent_id: str = ""
    ambient_trigger_every_n: int = 5


# ── router ────────────────────────────────────────────────────────────

def make_collab_router() -> APIRouter:
    r = APIRouter(prefix="/node/collab", tags=["collab"])

    # ── spaces ──

    @r.post("/spaces")
    def create_space(body: CreateSpaceBody):
        space = S.create_space(body.name, body.description, body.owner_id)
        return space.model_dump()

    @r.get("/spaces")
    def list_spaces():
        return {"spaces": [s.model_dump() for s in S.list_spaces()]}

    @r.get("/spaces/{space_id}")
    def get_space(space_id: str):
        space = S.get_space(space_id)
        if not space:
            raise HTTPException(404, "space not found")
        members = S.get_members(space_id)
        return {**space.model_dump(), "members": [m.model_dump() for m in members]}

    @r.delete("/spaces/{space_id}")
    def delete_space(space_id: str):
        if not S.delete_space(space_id):
            raise HTTPException(404, "space not found")
        return {"ok": True}

    @r.post("/spaces/{space_id}/join")
    def join_space(space_id: str, body: JoinSpaceBody):
        space = S.get_space(space_id)
        if not space:
            raise HTTPException(404, "space not found")
        if body.invite_code != space.invite_code:
            raise HTTPException(403, "invalid invite code")
        member = S.Member(user_id=body.user_id, display_name=body.display_name)
        S.add_member(space_id, member)
        return member.model_dump()

    @r.patch("/spaces/{space_id}/ambient")
    def set_ambient(space_id: str, body: AmbientConfigBody):
        space = S.get_space(space_id)
        if not space:
            raise HTTPException(404, "space not found")
        space.ambient_enabled = body.ambient_enabled
        space.ambient_agent_id = body.ambient_agent_id
        space.ambient_trigger_every_n = body.ambient_trigger_every_n
        S.save_space(space)
        return space.model_dump()

    # ── messages ──

    @r.get("/spaces/{space_id}/messages")
    def get_messages(space_id: str, limit: int = 50, before: Optional[str] = None):
        if not S.get_space(space_id):
            raise HTTPException(404, "space not found")
        msgs = S.get_messages(space_id, limit=limit, before=before)
        return {"messages": [m.model_dump() for m in msgs]}

    @r.post("/spaces/{space_id}/messages")
    async def post_message(space_id: str, body: PostMessageBody):
        space = S.get_space(space_id)
        if not space:
            raise HTTPException(404, "space not found")
        msg = S.post_message(
            space_id, body.sender_id, body.sender_type, body.content, body.thread_id
        )
        await broadcast(space_id, {"type": "message", "data": msg.model_dump()})

        # @mention dispatch (background)
        if msg.mentions:
            asyncio.create_task(_dispatch_mentions(space_id, msg))

        # ambient trigger (background)
        if space.ambient_enabled and space.ambient_agent_id:
            count = S.message_count(space_id)
            if count % space.ambient_trigger_every_n == 0:
                asyncio.create_task(_ambient_trigger(space_id, space))

        return msg.model_dump()

    # ── SSE stream ──

    @r.get("/spaces/{space_id}/stream")
    async def stream_space(space_id: str):
        if not S.get_space(space_id):
            raise HTTPException(404, "space not found")

        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        _get_queues(space_id).append(q)

        async def gen():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        chunk = await asyncio.wait_for(q.get(), timeout=20.0)
                        yield chunk
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                try:
                    _get_queues(space_id).remove(q)
                except ValueError:
                    pass

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ── agents ──

    @r.get("/agents")
    def list_agents():
        from agcl.collab.agents import list_agents as _list
        return {"agents": [a.model_dump() for a in _list()]}

    @r.post("/agents")
    def register_agent(body: dict):
        from agcl.collab.agents import AgentProfile, register_agent as _reg
        profile = AgentProfile(**body)
        _reg(profile)
        return profile.model_dump()

    @r.post("/spaces/{space_id}/agents")
    def add_agent(space_id: str, body: dict):
        from agcl.collab.agents import add_agent_to_space
        agent_id = body.get("agent_id") or body.get("name")
        if not agent_id:
            raise HTTPException(400, "agent_id required")
        if not add_agent_to_space(space_id, agent_id):
            raise HTTPException(404, "space not found")
        return {"ok": True, "agent_id": agent_id}

    @r.delete("/spaces/{space_id}/agents/{agent_id}")
    def remove_agent(space_id: str, agent_id: str):
        from agcl.collab.agents import remove_agent_from_space
        remove_agent_from_space(space_id, agent_id)
        return {"ok": True}

    # ── tasks + presence (attach from their modules) ──
    from agcl.collab.tasks import attach_task_routes
    from agcl.collab.presence import attach_presence_routes
    attach_task_routes(r)
    attach_presence_routes(r)

    return r


# ── background helpers ────────────────────────────────────────────────

async def _dispatch_mentions(space_id: str, msg: S.Message) -> None:
    """Dispatch @mentions to registered agents in the space."""
    from agcl.collab.agents import dispatch_mention
    space = S.get_space(space_id)
    if not space:
        return
    for name in msg.mentions:
        agent_id = next((aid for aid in space.agent_ids if aid == name), None)
        if agent_id:
            await dispatch_mention(space_id, agent_id, msg, broadcast)


async def _ambient_trigger(space_id: str, space: S.Space) -> None:
    """Invoke the ambient agent with recent context."""
    from agcl.collab.agents import invoke_agent
    recent = S.get_messages(space_id, limit=10)
    context = "\n".join(f"{m.sender_id}: {m.content}" for m in recent)
    prompt = f"[ambient] Observe this conversation and offer a helpful suggestion if relevant:\n\n{context}"
    response = await invoke_agent(space.ambient_agent_id, prompt)
    if response:
        ambient_msg = S.post_message(
            space_id, space.ambient_agent_id, "agent", response, ambient=True
        )
        await broadcast(space_id, {"type": "message", "data": ambient_msg.model_dump()})
