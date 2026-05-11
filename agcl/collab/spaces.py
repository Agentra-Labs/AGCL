"""
agcl.collab.spaces — Space, Member, and Message models + persistence.

Key schema (under STATE_DIR/collab/):
    collab_space_<id>.json          Space object
    collab_spaces_index.json        list of space ids
    collab_invite_<code>.json       {"space_id": ...}
    collab_messages_<space_id>.json list of Message objects
"""
from __future__ import annotations

import secrets
import time
import uuid
from typing import List, Optional

from pydantic import BaseModel, Field

from agcl.collab.store import kv_get, kv_set, kv_delete, list_append, list_get, list_replace


# ── Models ────────────────────────────────────────────────────────────

class Member(BaseModel):
    user_id: str
    display_name: str
    avatar_url: str = ""
    joined_at: float = Field(default_factory=time.time)


class Space(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    invite_code: str = Field(default_factory=lambda: secrets.token_urlsafe(6))
    created_at: float = Field(default_factory=time.time)
    member_ids: List[str] = Field(default_factory=list)
    agent_ids: List[str] = Field(default_factory=list)
    # ambient intelligence config
    ambient_enabled: bool = False
    ambient_agent_id: str = ""
    ambient_trigger_every_n: int = 5
    _message_count: int = 0  # not persisted in model; tracked separately


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    space_id: str
    sender_id: str
    sender_type: str  # "human" | "agent"
    content: str
    created_at: float = Field(default_factory=time.time)
    thread_id: Optional[str] = None
    mentions: List[str] = Field(default_factory=list)
    ambient: bool = False
    partial: bool = False


# ── Space CRUD ────────────────────────────────────────────────────────

def create_space(name: str, description: str = "", owner_id: str = "owner") -> Space:
    space = Space(name=name, description=description, member_ids=[owner_id])
    kv_set(f"collab_space_{space.id}", space.model_dump())
    kv_set(f"collab_invite_{space.invite_code}", {"space_id": space.id})
    # add to index
    index: list = kv_get("collab_spaces_index") or []
    index.append(space.id)
    kv_set("collab_spaces_index", index)
    return space


def get_space(space_id: str) -> Optional[Space]:
    data = kv_get(f"collab_space_{space_id}")
    return Space(**data) if data else None


def save_space(space: Space) -> None:
    kv_set(f"collab_space_{space.id}", space.model_dump())


def list_spaces() -> List[Space]:
    index: list = kv_get("collab_spaces_index") or []
    spaces = []
    for sid in index:
        s = get_space(sid)
        if s:
            spaces.append(s)
    return spaces


def delete_space(space_id: str) -> bool:
    space = get_space(space_id)
    if not space:
        return False
    kv_delete(f"collab_space_{space_id}")
    kv_delete(f"collab_invite_{space.invite_code}")
    kv_delete(f"collab_messages_{space_id}")
    index: list = kv_get("collab_spaces_index") or []
    kv_set("collab_spaces_index", [i for i in index if i != space_id])
    return True


def resolve_invite(code: str) -> Optional[str]:
    """Return space_id for an invite code, or None."""
    data = kv_get(f"collab_invite_{code}")
    return data["space_id"] if data else None


def add_member(space_id: str, member: Member) -> Optional[Space]:
    space = get_space(space_id)
    if not space:
        return None
    if member.user_id not in space.member_ids:
        space.member_ids.append(member.user_id)
    # persist member profile
    kv_set(f"collab_member_{space_id}_{member.user_id}", member.model_dump())
    save_space(space)
    return space


def get_member(space_id: str, user_id: str) -> Optional[Member]:
    data = kv_get(f"collab_member_{space_id}_{user_id}")
    return Member(**data) if data else None


def get_members(space_id: str) -> List[Member]:
    space = get_space(space_id)
    if not space:
        return []
    members = []
    for uid in space.member_ids:
        m = get_member(space_id, uid)
        if m:
            members.append(m)
    return members


# ── Message CRUD ──────────────────────────────────────────────────────

def _parse_mentions(content: str) -> List[str]:
    """Extract @name tokens from message content."""
    import re
    return re.findall(r"@([\w\-]+)", content)


def post_message(
    space_id: str,
    sender_id: str,
    sender_type: str,
    content: str,
    thread_id: Optional[str] = None,
    ambient: bool = False,
    partial: bool = False,
) -> Message:
    mentions = _parse_mentions(content)
    msg = Message(
        space_id=space_id,
        sender_id=sender_id,
        sender_type=sender_type,
        content=content,
        thread_id=thread_id,
        mentions=mentions,
        ambient=ambient,
        partial=partial,
    )
    list_append(f"collab_messages_{space_id}", msg.model_dump())
    return msg


def get_messages(
    space_id: str,
    limit: int = 50,
    before: Optional[str] = None,
) -> List[Message]:
    all_msgs = list_get(f"collab_messages_{space_id}")
    messages = [Message(**m) for m in all_msgs]
    if before:
        idx = next((i for i, m in enumerate(messages) if m.id == before), len(messages))
        messages = messages[:idx]
    return messages[-limit:]


def update_message(space_id: str, msg_id: str, **kwargs) -> Optional[Message]:
    """Update fields on an existing message (e.g. partial → False)."""
    all_msgs = list_get(f"collab_messages_{space_id}")
    updated = None
    for i, m in enumerate(all_msgs):
        if m["id"] == msg_id:
            m.update(kwargs)
            all_msgs[i] = m
            updated = Message(**m)
            break
    if updated:
        list_replace(f"collab_messages_{space_id}", all_msgs)
    return updated


def message_count(space_id: str) -> int:
    return len(list_get(f"collab_messages_{space_id}"))
