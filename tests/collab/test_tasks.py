"""Tests for agcl.collab.tasks — task lifecycle."""
import pytest


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    import agcl.config as cfg
    monkeypatch.setattr(cfg, "STATE_DIR", str(tmp_path))
    import agcl.collab.store as store_mod
    def _patched_root():
        from pathlib import Path
        p = Path(str(tmp_path)) / "collab"
        p.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(store_mod, "_root", _patched_root)


def test_create_and_list_tasks():
    from agcl.collab import create_space
    from agcl.collab.tasks import create_task, list_tasks
    space = create_space("task-space")
    t = create_task(space.id, "Fix the bug", description="details here")
    assert t.status == "open"
    tasks = list_tasks(space.id)
    assert len(tasks) == 1
    assert tasks[0].title == "Fix the bug"


def test_update_task_status():
    from agcl.collab import create_space
    from agcl.collab.tasks import create_task, update_task
    space = create_space("s")
    t = create_task(space.id, "Do thing")
    updated = update_task(t.id, space.id, status="in_progress")
    assert updated is not None
    assert updated.status == "in_progress"


def test_delete_task():
    from agcl.collab import create_space
    from agcl.collab.tasks import create_task, delete_task, list_tasks
    space = create_space("s")
    t = create_task(space.id, "Temp task")
    assert delete_task(t.id, space.id) is True
    assert list_tasks(space.id) == []


def test_task_from_message():
    from agcl.collab import create_space, post_message
    from agcl.collab.tasks import create_task
    space = create_space("s")
    msg = post_message(space.id, "alice", "human", "We need to refactor the auth module")
    t = create_task(space.id, title=msg.content[:80], source_message_id=msg.id)
    assert t.source_message_id == msg.id
    assert "refactor" in t.title


def test_filter_tasks_by_status():
    from agcl.collab import create_space
    from agcl.collab.tasks import create_task, update_task, list_tasks
    space = create_space("s")
    t1 = create_task(space.id, "open task")
    t2 = create_task(space.id, "done task")
    update_task(t2.id, space.id, status="done")
    open_tasks = list_tasks(space.id, status="open")
    assert len(open_tasks) == 1
    assert open_tasks[0].id == t1.id
