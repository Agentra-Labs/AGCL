"""Tests for /node/collab/* HTTP routes."""
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


def _make_client(monkeypatch, tmp_path):
    """Build a TestClient with the collab router mounted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agcl.collab.router import make_collab_router
    app = FastAPI()
    app.include_router(make_collab_router())
    return TestClient(app)


def test_create_and_list_spaces(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    r = client.post("/node/collab/spaces", json={"name": "test-space"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "test-space"
    assert data["invite_code"]

    r2 = client.get("/node/collab/spaces")
    assert r2.status_code == 200
    assert len(r2.json()["spaces"]) == 1


def test_join_space(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    space = client.post("/node/collab/spaces", json={"name": "joinable"}).json()
    r = client.post(f"/node/collab/spaces/{space['id']}/join", json={
        "invite_code": space["invite_code"], "user_id": "alice", "display_name": "Alice"
    })
    assert r.status_code == 200
    assert r.json()["user_id"] == "alice"


def test_join_wrong_code(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    space = client.post("/node/collab/spaces", json={"name": "s"}).json()
    r = client.post(f"/node/collab/spaces/{space['id']}/join", json={
        "invite_code": "wrongcode", "user_id": "x"
    })
    assert r.status_code == 403


def test_post_and_get_messages(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    space = client.post("/node/collab/spaces", json={"name": "chat"}).json()
    sid = space["id"]
    r = client.post(f"/node/collab/spaces/{sid}/messages", json={
        "sender_id": "alice", "sender_type": "human", "content": "hello world"
    })
    assert r.status_code == 200
    msg = r.json()
    assert msg["content"] == "hello world"

    r2 = client.get(f"/node/collab/spaces/{sid}/messages")
    assert len(r2.json()["messages"]) == 1


def test_get_space_not_found(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    r = client.get("/node/collab/spaces/nonexistent")
    assert r.status_code == 404
