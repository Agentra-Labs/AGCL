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
