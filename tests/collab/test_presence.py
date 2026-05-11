"""Tests for agcl.collab.presence — heartbeats and ambient trigger."""
import time
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


def test_set_and_get_presence():
    from agcl.collab.presence import set_presence, get_online
    set_presence("space1", "alice", "online", ttl=30.0)
    online = get_online("space1")
    assert online.get("alice") == "online"


def test_presence_expires(monkeypatch):
    from agcl.collab.presence import set_presence, get_online
    set_presence("space2", "bob", "online", ttl=0.01)
    time.sleep(0.05)
    online = get_online("space2")
    assert "bob" not in online


def test_ambient_trigger_fires(monkeypatch, tmp_path):
    """After N messages, ambient agent should be invoked."""
    from agcl.collab import create_space, save_space, post_message
    import agcl.collab.agents as agents_mod

    invoked = []
    async def mock_invoke(agent_id, prompt):
        invoked.append((agent_id, prompt))
        return "Here is a suggestion"

    monkeypatch.setattr(agents_mod, "invoke_agent", mock_invoke)

    space = create_space("ambient-space")
    space.ambient_enabled = True
    space.ambient_agent_id = "ambient-bot"
    space.ambient_trigger_every_n = 3
    save_space(space)

    # post 3 messages — trigger should fire on 3rd
    import asyncio
    from agcl.collab.router import _ambient_trigger

    for i in range(3):
        post_message(space.id, "alice", "human", f"msg {i}")

    asyncio.run(_ambient_trigger(space.id, space))
    assert len(invoked) == 1
    assert invoked[0][0] == "ambient-bot"
