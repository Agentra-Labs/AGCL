"""
HTTP surface for the toolkit subsystem.

Exposes /node/toolkit/* routes that let a GUI or tool platform drive
every adapter at runtime: discover what's configured, ping each
backend, route a chat request through whichever provider is active,
trigger a checkpoint sync, generate Dockerfile / Helm charts, push
events to Cloudflare relay, and so on.

Mounted by agcl.node.build_node_app() alongside the main /node/*
router so everything stays under one auth-gated prefix.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import (
    cloudflare, discord, docker, gcp, k8s,
    litellm_gw, ollama, registry, tgi, vllm, webrtc,
)


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages:    List[Dict[str, Any]]
    model:       Optional[str] = None
    stream:      bool = False
    max_tokens:  Optional[int] = None
    temperature: Optional[float] = None


class StateBlob(BaseModel):
    agent_id: str
    state:    Dict[str, Any]
    ttl:      int = 86400


class CheckpointBlob(BaseModel):
    topic_id: str
    name:     str
    data_b64: str           # base64-encoded bytes


class RelayEvent(BaseModel):
    session_id: str
    event:      Dict[str, Any]


class WriteManifestRequest(BaseModel):
    out_dir: Optional[str] = None
    gpu:     bool = False
    with_ollama:  bool = True
    with_litellm: bool = True
    with_redis:   bool = True


class WebRTCSdp(BaseModel):
    session_id: str
    sdp:        Dict[str, Any]


class WebRTCIce(BaseModel):
    session_id: str
    side:       str         # "a" (node) or "b" (peer)
    candidate:  Dict[str, Any]


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------

def make_toolkit_router() -> APIRouter:
    r = APIRouter(prefix="/node/toolkit", tags=["toolkit"])

    # ── discovery ──────────────────────────────────────────────────
    @r.get("/discover")
    def discover():
        return {"adapters": registry.discover()}

    # ── ping each adapter (cheap reachability checks) ──────────────
    @r.get("/ping/{adapter}")
    async def ping_adapter(adapter: str):
        modules = {
            "litellm":    litellm_gw.ping,
            "ollama":     ollama.ping,
            "vllm":       vllm.ping,
            "tgi":        tgi.ping,
            "cloudflare": cloudflare.ping,
            "discord":    discord.ping,
        }
        if adapter == "redis":
            store = registry.state_store()
            return await store.ping()
        if adapter == "s3":
            store = registry.checkpoint_store()
            return await store.ping()
        if adapter == "docker":
            return docker.ping()
        if adapter == "k8s":
            return k8s.ping()
        if adapter == "gcp":
            return gcp.ping()
        fn = modules.get(adapter)
        if fn is None:
            raise HTTPException(404, f"unknown adapter: {adapter!r}")
        return await fn()

    # ── chat (gateway-aware) ───────────────────────────────────────
    @r.post("/chat")
    async def chat(body: ChatRequest):
        client = registry.get_chat_client()
        if client is None:
            raise HTTPException(
                503,
                "no toolkit chat backend configured "
                "(set AGCL_LLM_BASE_URL or VLLM_HOST or OLLAMA_HOST)",
            )
        model = body.model or getattr(client, "default_model", None) or "smart"
        if body.stream:
            async def gen():
                # Own the client for the lifetime of the stream so the
                # httpx pool closes inside the request's loop.
                try:
                    async for chunk in client.stream(
                        body.messages, model=model,
                        max_tokens=body.max_tokens, temperature=body.temperature,
                    ):
                        yield f"data: {json.dumps({'delta': chunk})}\n\n"
                    yield "data: [DONE]\n\n"
                finally:
                    try: await client.aclose()
                    except Exception: pass
            return StreamingResponse(gen(), media_type="text/event-stream")
        try:
            return await client.chat(
                body.messages, model=model,
                max_tokens=body.max_tokens, temperature=body.temperature,
            )
        finally:
            try: await client.aclose()
            except Exception: pass

    # ── distributed state ──────────────────────────────────────────
    @r.put("/state")
    async def save_state(body: StateBlob):
        s = registry.state_store()
        await s.save_state(body.agent_id, body.state, ttl=body.ttl)
        return {"ok": True, "kind": s.kind}

    @r.get("/state/{agent_id}")
    async def load_state(agent_id: str):
        s = registry.state_store()
        data = await s.load_state(agent_id)
        if data is None:
            raise HTTPException(404, "no such state")
        return {"agent_id": agent_id, "state": data, "kind": s.kind}

    @r.delete("/state/{agent_id}")
    async def delete_state(agent_id: str):
        s = registry.state_store()
        ok = await s.delete_state(agent_id)
        return {"ok": ok}

    # ── checkpoint store ───────────────────────────────────────────
    @r.put("/checkpoint")
    async def save_checkpoint(body: CheckpointBlob):
        import base64
        try:
            data = base64.b64decode(body.data_b64)
        except Exception as e:
            raise HTTPException(400, f"data_b64 not valid base64: {e}")
        s = registry.checkpoint_store()
        await s.save_blob(body.topic_id, body.name, data)
        return {"ok": True, "kind": s.kind, "size": len(data)}

    @r.get("/checkpoint/{topic_id}/{name}")
    async def load_checkpoint(topic_id: str, name: str):
        import base64
        s = registry.checkpoint_store()
        try:
            data = await s.load_blob(topic_id, name)
        except FileNotFoundError:
            raise HTTPException(404, "checkpoint not found")
        return {"topic_id": topic_id, "name": name,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "size": len(data), "kind": s.kind}

    @r.delete("/checkpoint/{topic_id}")
    async def delete_topic_checkpoint(topic_id: str):
        s = registry.checkpoint_store()
        ok = await s.delete_topic(topic_id)
        return {"ok": ok}

    @r.get("/checkpoints")
    async def list_topic_checkpoints():
        s = registry.checkpoint_store()
        return {"topics": await s.list_topics(), "kind": s.kind}

    # ── Cloudflare edge relay ──────────────────────────────────────
    @r.post("/relay/publish")
    async def relay_publish(body: RelayEvent):
        return await cloudflare.publish(body.session_id, body.event)

    # ── Docker / K8s manifest emitters ─────────────────────────────
    @r.post("/manifest/docker")
    def emit_docker(body: WriteManifestRequest):
        out = body.out_dir or "deploy"
        result = docker.write_to(out)
        # Override compose with caller's flags after default write.
        compose_path = f"{out}/docker-compose.yml"
        with open(compose_path, "w") as f:
            f.write(docker.compose(
                with_ollama=body.with_ollama,
                with_litellm=body.with_litellm,
                with_redis=body.with_redis,
                gpu=body.gpu,
            ))
        return result

    @r.post("/manifest/k8s")
    def emit_k8s(body: WriteManifestRequest):
        out = body.out_dir or "deploy/agcl-chart"
        chart = k8s.write_chart(out)
        manifests = k8s.write_manifests(out + "/manifests" if not out.endswith("k8s") else out)
        return {"chart": chart, "manifests": manifests}

    @r.post("/manifest/gcp")
    def emit_gcp(body: WriteManifestRequest):
        # Uses default project/service/region; override env via env vars
        # before this call, or call gcp.write_to() directly.
        import os as _os
        return gcp.write_to(
            out_dir=body.out_dir or "deploy/gcp",
            project=_os.getenv("GOOGLE_CLOUD_PROJECT", "PROJECT_ID"),
            service=_os.getenv("AGCL_GCP_SERVICE", "agcl-node"),
            region=_os.getenv("AGCL_GCP_REGION",  "us-central1"),
        )

    # ── WebRTC signaling (lightweight) ─────────────────────────────
    @r.get("/webrtc/status")
    def webrtc_status():
        return webrtc.status()

    @r.post("/webrtc/offer")
    async def webrtc_offer(body: WebRTCSdp):
        await webrtc.post_offer(body.session_id, body.sdp)
        return {"ok": True}

    @r.get("/webrtc/offer/{session_id}")
    async def webrtc_get_offer(session_id: str):
        sdp = await webrtc.get_offer(session_id)
        if sdp is None:
            raise HTTPException(404, "no offer for this session yet")
        return {"sdp": sdp}

    @r.post("/webrtc/answer")
    async def webrtc_answer(body: WebRTCSdp):
        await webrtc.post_answer(body.session_id, body.sdp)
        return {"ok": True}

    @r.get("/webrtc/answer/{session_id}")
    async def webrtc_get_answer(session_id: str):
        sdp = await webrtc.get_answer(session_id)
        if sdp is None:
            raise HTTPException(404, "no answer yet")
        return {"sdp": sdp}

    @r.post("/webrtc/ice")
    async def webrtc_ice(body: WebRTCIce):
        await webrtc.add_ice(body.session_id, body.side, body.candidate)
        return {"ok": True}

    @r.get("/webrtc/ice/{session_id}/{side}")
    async def webrtc_pull_ice(session_id: str, side: str):
        return {"candidates": await webrtc.pull_ice(session_id, side)}

    return r
