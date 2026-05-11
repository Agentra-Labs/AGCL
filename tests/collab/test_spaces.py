"""Tests for agcl.collab data layer — spaces, members, messages."""
import os
import tempfile
import pytest


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    """Each test gets its own STATE_DIR so files don't bleed between tests."""
    monkeypatch.setenv("AGCL_STATE_DIR", str(tmp_path))
    # patch the config module so store._root() picks up the tmp dir
    import agcl.config as cfg
    monkeypatch.setattr(cfg, "STATE_DIR", str(tmp_path))
    # also patch store module's _root to use tmp_path
    import agcl.collab.store as store_mod
    original_root = store_mod._root

    def _patched_root():
        from pathlib import Path
        p = Path(str(tmp_path)) / "collab"
        p.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(store_mod, "_root", _patched_root)
    yield


def test_create_and_get_space():
    from agcl.collab import create_space, get_space
    space = create_space("Test Space", description="hello")
    assert space.id
    assert space.invite_code
    fetched = get_space(space.id)
    assert fetched is not None
    assert fetched.name == "Test Space"
    assert fetched.description == "hello"


def test_list_spaces():
    from agcl.collab import create_space, list_spaces
    create_space("A")
    create_space("B")
    spaces = list_spaces()
    assert len(spaces) == 2
    names = {s.name for s in spaces}
    assert names == {"A", "B"}


def test_invite_code_resolves():
    from agcl.collab import create_space, resolve_invite
    space = create_space("Invite Test")
    resolved = resolve_invite(space.invite_code)
    assert resolved == space.id


def test_invalid_invite_returns_none():
    from agcl.collab import resolve_invite
    assert resolve_invite("badcode") is None


def test_add_member():
    from agcl.collab import create_space, add_member, get_members, Member
    space = create_space("Members Test")
    member = Member(user_id="alice", display_name="Alice")
    updated = add_member(space.id, member)
    assert updated is not None
    assert "alice" in updated.member_ids
    members = get_members(space.id)
    assert any(m.user_id == "alice" for m in members)


def test_add_member_idempotent():
    from agcl.collab import create_space, add_member, get_space, Member
    space = create_space("Idempotent")
    m = Member(user_id="bob", display_name="Bob")
    add_member(space.id, m)
    add_member(space.id, m)
    s = get_space(space.id)
    assert s.member_ids.count("bob") == 1


def test_post_and_get_messages():
    from agcl.collab import create_space, post_message, get_messages
    space = create_space("Chat")
    post_message(space.id, "alice", "human", "Hello world")
    post_message(space.id, "bob", "human", "Hi there")
    msgs = get_messages(space.id)
    assert len(msgs) == 2
    assert msgs[0].content == "Hello world"
    assert msgs[1].content == "Hi there"


def test_message_pagination():
    from agcl.collab import create_space, post_message, get_messages
    space = create_space("Paginate")
    ids = []
    for i in range(10):
        m = post_message(space.id, "u", "human", f"msg {i}")
        ids.append(m.id)
    # get last 5
    msgs = get_messages(space.id, limit=5)
    assert len(msgs) == 5
    assert msgs[-1].content == "msg 9"
    # get before a specific id
    msgs_before = get_messages(space.id, before=ids[5])
    assert len(msgs_before) == 5
    assert all(m.content != "msg 5" for m in msgs_before)


def test_mention_parsing():
    from agcl.collab import create_space, post_message
    space = create_space("Mentions")
    msg = post_message(space.id, "alice", "human", "Hey @bob and @carol!")
    assert "bob" in msg.mentions
    assert "carol" in msg.mentions


def test_ambient_message_flag():
    from agcl.collab import create_space, post_message, get_messages
    space = create_space("Ambient")
    post_message(space.id, "agent-1", "agent", "A suggestion", ambient=True)
    msgs = get_messages(space.id)
    assert msgs[0].ambient is True


def test_delete_space():
    from agcl.collab import create_space, delete_space, get_space, list_spaces
    space = create_space("ToDelete")
    assert delete_space(space.id) is True
    assert get_space(space.id) is None
    assert all(s.id != space.id for s in list_spaces())
