"""Tests for agcl.collab.agents — registry and @mention dispatch."""
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


def test_register_and_list_agent():
    from agcl.collab.agents import AgentProfile, register_agent, list_agents
    a = AgentProfile(id="test-agent", name="test-agent", description="A test agent")
    register_agent(a)
    agents = list_agents()
    assert any(x.id == "test-agent" for x in agents)


def test_add_remove_agent_from_space():
    from agcl.collab import create_space, get_space
    from agcl.collab.agents import AgentProfile, register_agent, add_agent_to_space, remove_agent_from_space
    space = create_space("agent-space")
    a = AgentProfile(id="bot", name="bot")
    register_agent(a)
    add_agent_to_space(space.id, "bot")
    s = get_space(space.id)
    assert "bot" in s.agent_ids
    remove_agent_from_space(space.id, "bot")
    s2 = get_space(space.id)
    assert "bot" not in s2.agent_ids


@pytest.mark.asyncio
async def test_dispatch_mention_posts_response(monkeypatch):
    from agcl.collab import create_space, post_message, get_messages
    from agcl.collab.agents import AgentProfile, register_agent, add_agent_to_space, dispatch_mention

    space = create_space("mention-space")
    a = AgentProfile(id="mock-agent", name="mock-agent", source="plugin")
    register_agent(a)
    add_agent_to_space(space.id, "mock-agent")

    # mock invoke_agent
    import agcl.collab.agents as agents_mod
    monkeypatch.setattr(agents_mod, "invoke_agent", lambda aid, prompt: _async_return("mocked response"))

    msg = post_message(space.id, "alice", "human", "hey @mock-agent hello")
    broadcasts = []
    async def fake_broadcast(sid, event): broadcasts.append(event)

    await dispatch_mention(space.id, "mock-agent", msg, fake_broadcast)

    msgs = get_messages(space.id)
    agent_msgs = [m for m in msgs if m.sender_type == "agent"]
    assert any("mocked response" in m.content for m in agent_msgs)


async def _async_return(val):
    return val
