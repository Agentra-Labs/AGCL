"""
Tool manifest: every external-callable AGCL operation in one place.

Each entry has:
    name        snake-case dotted id, e.g. "agcl.mas.run"
    description one-liner an LLM will see in a tool list
    schema      JSONSchema-shaped input description
    handler     plain Python callable (sync) returning JSON-able result

Adapters (MCP, OpenAgents, Slack, OpenAPI) consume `TOOLS` and
translate the entries into their platform-native shape. Plugins can
extend the registry at runtime via `register_tool(...)`.

Handlers are deliberately dumb wrappers around the existing AGCL
modules. They never set up sessions or load models eagerly - the same
lazy-import pattern the node API uses keeps adapter cold-start fast.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List


# ---------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------

def _h_mas_run(args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.recursive import build_from_config, RecursiveSession
    msg = (args.get("message") or "").strip()
    if not msg:
        return {"error": "message required"}
    sid = args.get("session_id") or "default"
    force_cloud = bool(args.get("force_cloud", False))
    force_continue = bool(args.get("force_continue", False))

    state = _SHARED_STATE
    if state.get("mas") is None:
        state["mas"] = build_from_config()
    if sid not in state["sessions"]:
        state["sessions"][sid] = RecursiveSession(state["mas"], verbose=False)
    sess = state["sessions"][sid]
    answer = asyncio.run(sess.turn(
        msg, force_cloud=force_cloud, force_continue=force_continue,
    ))
    return {
        "session_id": sid,
        "topic_id":   sess.current_topic_id,
        "answer":     answer,
    }


def _h_mas_status(args: Dict[str, Any]) -> Dict[str, Any]:
    sid = args.get("session_id") or "default"
    sess = _SHARED_STATE["sessions"].get(sid)
    if sess is None:
        return {"error": f"no session {sid!r}"}
    return {"session_id": sid, "status": sess.control.status()}


def _h_mas_pause(args: Dict[str, Any]) -> Dict[str, Any]:
    sid = args.get("session_id") or "default"
    sess = _SHARED_STATE["sessions"].get(sid)
    if sess is None:
        return {"error": f"no session {sid!r}"}
    sess.control.pause()
    return {"ok": True, "status": sess.control.status()}


def _h_mas_resume(args: Dict[str, Any]) -> Dict[str, Any]:
    sid = args.get("session_id") or "default"
    sess = _SHARED_STATE["sessions"].get(sid)
    if sess is None:
        return {"error": f"no session {sid!r}"}
    sess.control.resume()
    return {"ok": True, "status": sess.control.status()}


def _h_mas_halt(args: Dict[str, Any]) -> Dict[str, Any]:
    sid = args.get("session_id") or "default"
    sess = _SHARED_STATE["sessions"].get(sid)
    if sess is None:
        return {"error": f"no session {sid!r}"}
    sess.control.halt()
    return {"ok": True, "status": sess.control.status()}


def _h_mas_latent(args: Dict[str, Any]) -> Dict[str, Any]:
    sid = args.get("session_id") or "default"
    sess = _SHARED_STATE["sessions"].get(sid)
    if sess is None:
        return {"error": f"no session {sid!r}"}
    return {"session_id": sid, "snapshot": sess.control.latent_snapshot()}


def _h_chat_run(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fast prefix+cloud chat — no MAS training, no link weights touched.

    This is the alternate path adapters can use when MAS is overkill
    for the use case (e.g. a Discord bot that just needs a quick
    answer). Mirrors the exact session shape and ordering of the
    main.py `_run_chat` HTTP handler so the two are interchangeable
    against the same session_id.

    Composes:
        agcl.local_llm.generate_prefix(...)  — short llama.cpp prefix
        agcl.cloud.stream_continuation(...)  — cloud finishes the turn
        agcl.state                           — append + flush per turn
        agcl.context.append(...)             — recontextualize on overflow
    """
    from agcl import state, local_llm, cloud, context
    msg = (args.get("message") or "").strip()
    if not msg:
        return {"error": "message required"}
    sid = args.get("session_id") or "default"
    provider = args.get("provider") or None
    recovery = args.get("recovery_mode") or "natural"

    state.touch()

    async def _run() -> Dict[str, Any]:
        sess = state.get_session(sid)
        messages = sess["messages"]
        # Append user turn + recontextualize if needed (same as the
        # /chat/{sid} HTTP route does).
        messages = await context.append(messages, "user", msg)
        sess["messages"] = messages
        state.put_session(sid, sess)

        # Local prefix — best-effort. If the model isn't loaded / fails,
        # we just skip the prefix and let the cloud handle the turn cold.
        prefix = ""
        try:
            gp = local_llm.generate_prefix(messages)
            # generate_prefix returns (text, local_ms_seconds); be
            # tolerant of older single-value returns too.
            prefix = (gp[0] if isinstance(gp, tuple) else gp) or ""
            prefix = prefix.strip()
        except Exception:
            prefix = ""

        # Drain the cloud stream.
        full = prefix
        async for chunk in cloud.stream_continuation(
            messages, prefix, provider, recovery,
        ):
            full += chunk

        # Persist the assistant turn the same way the HTTP route does.
        sess["messages"] = await context.append(sess["messages"], "assistant", full)
        state.put_session(sid, sess)
        state.flush_session(sid)
        return {"answer": full, "prefix": prefix,
                "session_id": sid, "provider": provider}

    return asyncio.run(_run())


def _h_mini_test(args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.mini import runtime as M
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt required"}
    return M.get_trainer().test(
        prompt,
        max_new=int(args.get("max_new", 32)),
        temperature=float(args.get("temperature", 0.8)),
    )


def _h_mini_status(_args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.mini import runtime as M
    return M.status()


def _h_mini_start(_args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.mini import runtime as M
    t = M.get_trainer(); t.config.enabled = True; t.start()
    return {"ok": True, "status": t.status()}


def _h_mini_stop(_args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.mini import runtime as M
    t = M.get_trainer(); t.stop()
    return {"ok": True, "status": t.status()}


def _h_topics_list(_args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl import config as cfg
    from agcl.recursive import persistence as P
    return {"topics": P.list_topics(cfg.STATE_DIR)}


def _h_plugins_list(_args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl import plugins as P
    return {"plugins": P.loaded(), "commands": list(P.commands().keys())}


# ---- toolkit tools ---------------------------------------------------

def _h_toolkit_discover(_args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.toolkit import registry
    return {"adapters": registry.discover()}


def _h_toolkit_ping(args: Dict[str, Any]) -> Dict[str, Any]:
    name = (args.get("adapter") or "").strip()
    if not name:
        return {"error": "adapter required"}
    from agcl.toolkit import (
        cloudflare, discord, docker, k8s,
        litellm_gw, ollama, registry, tgi, vllm,
    )
    fns = {
        "litellm":    litellm_gw.ping,
        "ollama":     ollama.ping,
        "vllm":       vllm.ping,
        "tgi":        tgi.ping,
        "cloudflare": cloudflare.ping,
        "discord":    discord.ping,
    }
    if name in fns:
        return asyncio.run(fns[name]())
    if name == "redis":
        return asyncio.run(registry.state_store().ping())
    if name == "s3":
        return asyncio.run(registry.checkpoint_store().ping())
    if name == "docker":
        return docker.ping()
    if name == "k8s":
        return k8s.ping()
    return {"error": f"unknown adapter: {name!r}"}


def _h_toolkit_chat(args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.toolkit import registry
    msg = (args.get("message") or "").strip()
    if not msg:
        return {"error": "message required"}
    client = registry.get_chat_client()
    if client is None:
        return {"error": "no gateway configured "
                         "(set AGCL_LLM_BASE_URL, VLLM_HOST, or OLLAMA_HOST)"}
    model = args.get("model") or getattr(client, "default_model", None) or "smart"

    async def _run():
        try:
            return await client.chat(
                [{"role": "user", "content": msg}],
                model=model,
                max_tokens=args.get("max_tokens"),
                temperature=args.get("temperature"),
            )
        finally:
            await client.aclose()
    resp = asyncio.run(_run())
    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {"answer": content, "model": model, "raw": resp}


def _h_toolkit_emit_docker(args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.toolkit import docker as D
    out = args.get("out") or "deploy"
    gpu = bool(args.get("gpu", False))
    result = D.write_to(out)
    with open(f"{out}/docker-compose.yml", "w") as f:
        f.write(D.compose(gpu=gpu))
    return result


def _h_toolkit_emit_k8s(args: Dict[str, Any]) -> Dict[str, Any]:
    from agcl.toolkit import k8s as K
    out = args.get("out") or "deploy/agcl-chart"
    chart = K.write_chart(out)
    manifests = K.write_manifests(out + "/manifests")
    return {"chart": chart, "manifests": manifests}


def _h_toolkit_emit_gcp(args: Dict[str, Any]) -> Dict[str, Any]:
    import os
    from agcl.toolkit import gcp as G
    return G.write_to(
        out_dir=args.get("out") or "deploy/gcp",
        project=args.get("project") or os.getenv("GOOGLE_CLOUD_PROJECT", "PROJECT_ID"),
        service=args.get("service") or os.getenv("AGCL_GCP_SERVICE", "agcl-node"),
        region=args.get("region")  or os.getenv("AGCL_GCP_REGION", "us-central1"),
    )


# ---------------------------------------------------------------------
# Per-process state shared by all tool calls. Adapters that want a
# cleaner reset should call clear_state() between runs.
# ---------------------------------------------------------------------

_SHARED_STATE: Dict[str, Any] = {"mas": None, "sessions": {}}


def clear_state() -> None:
    _SHARED_STATE["mas"] = None
    _SHARED_STATE["sessions"] = {}


# ---------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "agcl.chat.run",
        "description": "Fast chat: local llama.cpp prefix + cloud continuation. "
                       "No MAS network, no training. Use this from bots that "
                       "need a quick reply without per-topic learning.",
        "schema": {
            "type": "object",
            "properties": {
                "message":       {"type": "string"},
                "session_id":    {"type": "string", "default": "default"},
                "provider":      {"type": "string", "enum": ["openai", "claude"]},
                "recovery_mode": {"type": "string", "enum": ["natural", "humor", "explicit"]},
            },
            "required": ["message"],
        },
        "handler": _h_chat_run,
    },
    {
        "name": "agcl.mas.run",
        "description": "Run a turn through AGCL's recursive multi-agent engine. "
                       "Returns a final answer string and topic id.",
        "schema": {
            "type": "object",
            "properties": {
                "message":        {"type": "string"},
                "session_id":     {"type": "string", "default": "default"},
                "force_cloud":    {"type": "boolean", "default": False},
                "force_continue": {"type": "boolean", "default": False},
            },
            "required": ["message"],
        },
        "handler": _h_mas_run,
    },
    {
        "name": "agcl.mas.status",
        "description": "Inspect the training-control state of an AGCL session.",
        "schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "default": "default"}},
        },
        "handler": _h_mas_status,
    },
    {
        "name": "agcl.mas.pause",
        "description": "Pause AGCL's recursive training loop at next step boundary.",
        "schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "default": "default"}},
        },
        "handler": _h_mas_pause,
    },
    {
        "name": "agcl.mas.resume",
        "description": "Resume an AGCL training loop paused via agcl.mas.pause.",
        "schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "default": "default"}},
        },
        "handler": _h_mas_resume,
    },
    {
        "name": "agcl.mas.halt",
        "description": "Halt AGCL's recursive training loop. The current turn aborts cleanly.",
        "schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "default": "default"}},
        },
        "handler": _h_mas_halt,
    },
    {
        "name": "agcl.mas.latent",
        "description": "Snapshot the most recent loop latent (truncated).",
        "schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string", "default": "default"}},
        },
        "handler": _h_mas_latent,
    },
    {
        "name": "agcl.mini.test",
        "description": "Generate text from AGCL's optional background mini-model.",
        "schema": {
            "type": "object",
            "properties": {
                "prompt":      {"type": "string"},
                "max_new":     {"type": "integer", "default": 32},
                "temperature": {"type": "number",  "default": 0.8},
            },
            "required": ["prompt"],
        },
        "handler": _h_mini_test,
    },
    {
        "name": "agcl.mini.status",
        "description": "Status of the mini-model background trainer.",
        "schema": {"type": "object", "properties": {}},
        "handler": _h_mini_status,
    },
    {
        "name": "agcl.mini.start",
        "description": "Enable the mini trainer and start its background thread.",
        "schema": {"type": "object", "properties": {}},
        "handler": _h_mini_start,
    },
    {
        "name": "agcl.mini.stop",
        "description": "Stop the mini trainer's background thread.",
        "schema": {"type": "object", "properties": {}},
        "handler": _h_mini_stop,
    },
    {
        "name": "agcl.topics.list",
        "description": "List saved trained-topic checkpoints in AGCL's state dir.",
        "schema": {"type": "object", "properties": {}},
        "handler": _h_topics_list,
    },
    {
        "name": "agcl.plugins.list",
        "description": "List discovered AGCL plugins and their CLI commands.",
        "schema": {"type": "object", "properties": {}},
        "handler": _h_plugins_list,
    },
    # ---- toolkit tools ----
    {
        "name": "agcl.toolkit.discover",
        "description": "Snapshot which infra/inference adapters (LiteLLM, Ollama, "
                       "vLLM, Redis, S3, Cloudflare, Docker, K8s) are configured "
                       "and importable.",
        "schema": {"type": "object", "properties": {}},
        "handler": _h_toolkit_discover,
    },
    {
        "name": "agcl.toolkit.ping",
        "description": "Reachability check against one toolkit adapter "
                       "(litellm | ollama | vllm | tgi | redis | s3 | "
                       "cloudflare | discord | docker | k8s).",
        "schema": {
            "type": "object",
            "properties": {"adapter": {"type": "string"}},
            "required": ["adapter"],
        },
        "handler": _h_toolkit_ping,
    },
    {
        "name": "agcl.toolkit.chat",
        "description": "Send one chat completion through whichever inference "
                       "backend is configured (gateway-aware: LiteLLM > vLLM > "
                       "Ollama). Returns the assistant message text.",
        "schema": {
            "type": "object",
            "properties": {
                "message":     {"type": "string"},
                "model":       {"type": "string"},
                "max_tokens":  {"type": "integer"},
                "temperature": {"type": "number"},
            },
            "required": ["message"],
        },
        "handler": _h_toolkit_chat,
    },
    {
        "name": "agcl.toolkit.emit_docker",
        "description": "Generate Dockerfile + docker-compose.yml + .dockerignore "
                       "for the current AGCL config.",
        "schema": {
            "type": "object",
            "properties": {
                "out": {"type": "string", "default": "deploy"},
                "gpu": {"type": "boolean", "default": False},
            },
        },
        "handler": _h_toolkit_emit_docker,
    },
    {
        "name": "agcl.toolkit.emit_k8s",
        "description": "Generate a Helm chart + standalone Kubernetes manifests "
                       "for the current AGCL config.",
        "schema": {
            "type": "object",
            "properties": {"out": {"type": "string", "default": "deploy/agcl-chart"}},
        },
        "handler": _h_toolkit_emit_k8s,
    },
    {
        "name": "agcl.toolkit.emit_gcp",
        "description": "Generate Cloud Run service.yaml + cloudbuild.yaml + "
                       "Secret Manager helper to deploy AGCL to GCP.",
        "schema": {
            "type": "object",
            "properties": {
                "out":     {"type": "string", "default": "deploy/gcp"},
                "project": {"type": "string"},
                "service": {"type": "string", "default": "agcl-node"},
                "region":  {"type": "string", "default": "us-central1"},
            },
        },
        "handler": _h_toolkit_emit_gcp,
    },
]


def register_tool(name: str, description: str, schema: Dict[str, Any],
                  handler: Callable[[Dict[str, Any]], Any]) -> None:
    """Plugins use this to inject additional tools into every adapter."""
    TOOLS.append({
        "name": name, "description": description,
        "schema": schema, "handler": handler,
    })


def tool_names() -> List[str]:
    return [t["name"] for t in TOOLS]


def call_tool(name: str, args: Dict[str, Any]) -> Any:
    for t in TOOLS:
        if t["name"] == name:
            return t["handler"](args or {})
    raise KeyError(f"no such tool: {name!r}")
