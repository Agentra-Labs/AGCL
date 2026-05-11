"""
agcl.collab.agents — agent profiles, registry, and peer participation.

Agents can be sourced from:
  - "plugin"  : registered via the existing agcl plugin system
  - "mas"     : the recursive MAS runtime
  - "remote"  : an external HTTP endpoint

Stored under collab_agent_<id>.json and collab_agents_index.json.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from agcl.collab.store import kv_get, kv_set, list_get, list_append


# ── Model ─────────────────────────────────────────────────────────────

class AgentProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    capabilities: List[str] = Field(default_factory=list)
    source: str = "plugin"          # "plugin" | "mas" | "remote"
    endpoint: Optional[str] = None  # URL for remote agents
    avatar_url: str = ""


# ── Registry CRUD ─────────────────────────────────────────────────────

def register_agent(profile: AgentProfile) -> AgentProfile:
    kv_set(f"collab_agent_{profile.id}", profile.model_dump())
    index: list = kv_get("collab_agents_index") or []
    if profile.id not in index:
        index.append(profile.id)
        kv_set("collab_agents_index", index)
    return profile


def get_agent(agent_id: str) -> Optional[AgentProfile]:
    data = kv_get(f"collab_agent_{agent_id}")
    return AgentProfile(**data) if data else None


def list_agents() -> List[AgentProfile]:
    index: list = kv_get("collab_agents_index") or []
    agents = []
    for aid in index:
        a = get_agent(aid)
        if a:
            agents.append(a)
    # also surface agents from the plugin registry dynamically
    try:
        from agcl import plugins as _plg
        for name, _plugin in (_plg._PLUGINS or {}).items():
            if not any(a.name == name for a in agents):
                agents.append(AgentProfile(id=name, name=name, source="plugin",
                                           description=f"Plugin: {name}"))
    except Exception:
        pass
    return agents


def delete_agent(agent_id: str) -> bool:
    from agcl.collab.store import kv_delete
    kv_delete(f"collab_agent_{agent_id}")
    index: list = kv_get("collab_agents_index") or []
    kv_set("collab_agents_index", [i for i in index if i != agent_id])
    return True


# ── Space agent membership ────────────────────────────────────────────

def add_agent_to_space(space_id: str, agent_id: str) -> bool:
    from agcl.collab.spaces import get_space, save_space
    space = get_space(space_id)
    if not space:
        return False
    if agent_id not in space.agent_ids:
        space.agent_ids.append(agent_id)
        save_space(space)
    return True


def remove_agent_from_space(space_id: str, agent_id: str) -> bool:
    from agcl.collab.spaces import get_space, save_space
    space = get_space(space_id)
    if not space:
        return False
    space.agent_ids = [a for a in space.agent_ids if a != agent_id]
    save_space(space)
    return True


# ── Invocation ────────────────────────────────────────────────────────

async def invoke_agent(agent_id: str, prompt: str) -> Optional[str]:
    """Invoke an agent and return its text response."""
    agent = get_agent(agent_id)
    if not agent:
        return None

    if agent.source == "remote" and agent.endpoint:
        return await _invoke_remote(agent.endpoint, prompt)

    if agent.source == "mas":
        return await _invoke_mas(prompt)

    # plugin / fallback: try MAS as default executor
    return await _invoke_mas(prompt)


async def _invoke_mas(prompt: str) -> Optional[str]:
    try:
        from agcl.recursive import build_from_config
        mas = await asyncio.to_thread(build_from_config)
        result = await asyncio.to_thread(
            lambda: asyncio.run(mas.turn(prompt))
        )
        return result
    except Exception as e:
        return f"[agent error: {e}]"


async def _invoke_remote(endpoint: str, prompt: str) -> Optional[str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(endpoint, json={"message": prompt})
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("text") or str(data)
    except Exception as e:
        return f"[remote agent error: {e}]"


async def dispatch_mention(
    space_id: str,
    agent_id: str,
    msg: Any,
    broadcast_fn: Callable,
) -> None:
    """Dispatch a @mention to an agent and post its response back."""
    from agcl.collab.spaces import post_message, update_message

    # Post a partial placeholder so the UI shows typing animation
    placeholder = post_message(space_id, agent_id, "agent", "…", partial=True)
    await broadcast_fn(space_id, {"type": "message", "data": placeholder.model_dump()})

    response = await invoke_agent(agent_id, msg.content)
    if response:
        # Update placeholder to final
        updated = update_message(space_id, placeholder.id, content=response, partial=False)
        if updated:
            await broadcast_fn(space_id, {"type": "message", "data": updated.model_dump()})
