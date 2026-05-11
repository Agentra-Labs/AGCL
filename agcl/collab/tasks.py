"""
agcl.collab.tasks — Task model, lifecycle, and kanban routes.

Routes (mounted via make_collab_router in router.py):
  POST   /node/collab/spaces/{id}/tasks
  GET    /node/collab/spaces/{id}/tasks
  PATCH  /node/collab/tasks/{task_id}
  DELETE /node/collab/tasks/{task_id}

Task status changes emit task_update events on the space SSE stream.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agcl.collab.store import kv_get, kv_set, list_get, list_append, list_replace


# ── Model ─────────────────────────────────────────────────────────────

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    space_id: str
    title: str
    description: str = ""
    status: str = "open"            # open | in_progress | done | blocked
    assignee_id: Optional[str] = None
    assignee_type: Optional[str] = None  # human | agent
    source_message_id: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    deadline: Optional[str] = None  # ISO datetime string


# ── Persistence ───────────────────────────────────────────────────────

def _tasks_key(space_id: str) -> str:
    return f"collab_tasks_{space_id}"


def create_task(
    space_id: str,
    title: str,
    description: str = "",
    assignee_id: Optional[str] = None,
    assignee_type: Optional[str] = None,
    source_message_id: Optional[str] = None,
    deadline: Optional[str] = None,
) -> Task:
    task = Task(
        space_id=space_id,
        title=title,
        description=description,
        assignee_id=assignee_id,
        assignee_type=assignee_type,
        source_message_id=source_message_id,
        deadline=deadline,
    )
    list_append(_tasks_key(space_id), task.model_dump())
    return task


def get_task(task_id: str, space_id: str) -> Optional[Task]:
    for t in list_get(_tasks_key(space_id)):
        if t["id"] == task_id:
            return Task(**t)
    return None


def list_tasks(space_id: str, status: Optional[str] = None) -> List[Task]:
    tasks = [Task(**t) for t in list_get(_tasks_key(space_id))]
    if status:
        tasks = [t for t in tasks if t.status == status]
    return tasks


def update_task(task_id: str, space_id: str, **kwargs) -> Optional[Task]:
    all_tasks = list_get(_tasks_key(space_id))
    updated = None
    for i, t in enumerate(all_tasks):
        if t["id"] == task_id:
            t.update({**kwargs, "updated_at": time.time()})
            all_tasks[i] = t
            updated = Task(**t)
            break
    if updated:
        list_replace(_tasks_key(space_id), all_tasks)
    return updated


def delete_task(task_id: str, space_id: str) -> bool:
    all_tasks = list_get(_tasks_key(space_id))
    new_tasks = [t for t in all_tasks if t["id"] != task_id]
    if len(new_tasks) == len(all_tasks):
        return False
    list_replace(_tasks_key(space_id), new_tasks)
    return True


# ── Router factory (called from router.py) ───────────────────────────

def attach_task_routes(r: APIRouter) -> None:
    """Attach task routes to an existing APIRouter."""
    from agcl.collab.spaces import get_space, get_messages

    class CreateTaskBody(BaseModel):
        title: Optional[str] = None
        description: str = ""
        assignee_id: Optional[str] = None
        assignee_type: Optional[str] = None
        source_message_id: Optional[str] = None
        deadline: Optional[str] = None

    class PatchTaskBody(BaseModel):
        title: Optional[str] = None
        description: Optional[str] = None
        status: Optional[str] = None
        assignee_id: Optional[str] = None
        assignee_type: Optional[str] = None
        deadline: Optional[str] = None

    @r.post("/spaces/{space_id}/tasks")
    async def post_task(space_id: str, body: CreateTaskBody):
        from agcl.collab.router import broadcast
        space = get_space(space_id)
        if not space:
            raise HTTPException(404, "space not found")

        title = body.title
        if not title and body.source_message_id:
            msgs = get_messages(space_id)
            src = next((m for m in msgs if m.id == body.source_message_id), None)
            if src:
                title = src.content[:80]
        if not title:
            raise HTTPException(400, "title required")

        task = create_task(
            space_id, title, body.description,
            body.assignee_id, body.assignee_type,
            body.source_message_id, body.deadline,
        )
        await broadcast(space_id, {"type": "task_update", "data": task.model_dump()})

        # auto-dispatch to agent if assigned
        if task.assignee_type == "agent" and task.assignee_id:
            asyncio.create_task(_agent_task_runner(space_id, task))

        return task.model_dump()

    @r.get("/spaces/{space_id}/tasks")
    def get_tasks(space_id: str, status: Optional[str] = None):
        if not get_space(space_id):
            raise HTTPException(404, "space not found")
        return {"tasks": [t.model_dump() for t in list_tasks(space_id, status)]}

    @r.patch("/tasks/{task_id}")
    async def patch_task(task_id: str, space_id: str, body: PatchTaskBody):
        from agcl.collab.router import broadcast
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        task = None
        # space_id passed as query param
        if space_id:
            task = update_task(task_id, space_id, **updates)
        if not task:
            raise HTTPException(404, "task not found")
        await broadcast(space_id, {"type": "task_update", "data": task.model_dump()})
        return task.model_dump()

    @r.delete("/tasks/{task_id}")
    def del_task(task_id: str, space_id: str):
        if not delete_task(task_id, space_id):
            raise HTTPException(404, "task not found")
        return {"ok": True}


async def _agent_task_runner(space_id: str, task: Task) -> None:
    """Background: run agent on task, post progress, mark done."""
    from agcl.collab.router import broadcast
    from agcl.collab.spaces import post_message
    from agcl.collab.agents import invoke_agent

    # mark in_progress
    updated = update_task(task.id, space_id, status="in_progress")
    if updated:
        await broadcast(space_id, {"type": "task_update", "data": updated.model_dump()})

    prompt = f"[task] {task.title}\n\n{task.description}"
    response = await invoke_agent(task.assignee_id, prompt)

    if response:
        post_message(space_id, task.assignee_id, "agent",
                     f"Task complete: {response}", thread_id=task.source_message_id)

    done = update_task(task.id, space_id, status="done")
    if done:
        await broadcast(space_id, {"type": "task_update", "data": done.model_dump()})
