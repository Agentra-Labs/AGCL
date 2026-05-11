"""
Node server — exposes one user's agcl install to an authorized GUI
client over HTTP + SSE with bearer-token auth and permissive CORS.

Lifecycle:
    1. user runs:   python main.py node --port 9876
    2. server prints a one-time auth key on stdout
    3. user pastes that key into a remote GUI (browser, same PC by default)
    4. GUI sends every request with Authorization: Bearer <key>

The node mode wraps the existing FastAPI app from `main`, gates every
route behind bearer auth (with a small public allowlist for liveness +
preflight), enables CORS so a browser-hosted GUI can talk to localhost,
and adds a /node/* router exposing every editable config, the recursive
MAS runtime (one-shot + SSE-streamed with live training progress), the
topic index, and session management.

The auth key lives in process memory. Killing the node invalidates it.
A custom key can be passed via `--auth-key <KEY>` (or env
`AGCL_NODE_AUTH`) for setups where the key needs to survive restarts.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel


# ============================================================
# Auth state (in-memory)
# ============================================================

_NODE_AUTH: Dict[str, Any] = {"key": None, "issued_at": None}


def issue_key(custom_key: Optional[str] = None) -> str:
    key = custom_key or secrets.token_urlsafe(32)
    _NODE_AUTH["key"] = key
    _NODE_AUTH["issued_at"] = time.time()
    return key


def current_key() -> Optional[str]:
    return _NODE_AUTH.get("key")


# Paths that bypass the auth middleware (still require auth at the
# endpoint level if they want to verify the key).
PUBLIC_PATHS = {
    "/node/health",
    "/node/auth/verify",
    "/node/dashboard",     # static HTML; auth happens on the data fetches
    "/docs", "/openapi.json", "/redoc",
}


async def _auth_middleware(request: Request, call_next):
    method = request.method
    path = request.url.path
    # CORS preflight + public liveness bypass auth; everything else requires
    # a valid bearer token.
    if method == "OPTIONS" or path in PUBLIC_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing bearer token"}, status_code=401)
    token = auth[len("Bearer "):]
    expected = _NODE_AUTH.get("key")
    if not expected or token != expected:
        return JSONResponse({"error": "invalid token"}, status_code=401)
    return await call_next(request)


# ============================================================
# MAS session storage (per node process)
# ============================================================

_mas_singleton: Dict[str, Any] = {"mas": None}
_mas_sessions: Dict[str, Any] = {}    # session_id -> RecursiveSession


def _build_mas_singleton():
    """Build the MAS once per process (loading models is expensive)."""
    if _mas_singleton["mas"] is None:
        from agcl.recursive import build_from_config
        _mas_singleton["mas"] = build_from_config()
    return _mas_singleton["mas"]


def _reset_mas_singleton() -> None:
    _mas_singleton["mas"] = None
    _mas_sessions.clear()


# ============================================================
# Helpers
# ============================================================

def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _persist_env(updates: Dict[str, str]) -> None:
    """Append/overwrite keys in .env at project root."""
    p = Path(".env")
    seen: Dict[str, bool] = {k: False for k in updates}
    out_lines: List[str] = []
    if p.exists():
        for line in p.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in updates:
                    out_lines.append(f"{k}={updates[k]}")
                    seen[k] = True
                    continue
            out_lines.append(line)
    for k, v in updates.items():
        if not seen[k]:
            out_lines.append(f"{k}={v}")
    p.write_text("\n".join(out_lines) + "\n")


# ============================================================
# Pydantic models for the API surface
# ============================================================

class ConfigPatch(BaseModel):
    default_cloud:        Optional[str] = None
    openai_model:         Optional[str] = None
    claude_model:         Optional[str] = None
    local_model_path:     Optional[str] = None
    local_n_ctx:          Optional[int] = None
    local_n_gpu_layers:   Optional[int] = None
    local_n_threads:      Optional[int] = None
    prefix_word_count:    Optional[int] = None
    mas_pattern:          Optional[str] = None
    mas_rounds:           Optional[int] = None
    mas_device:           Optional[str] = None
    mas_dtype:            Optional[str] = None
    idle_flush_sec:       Optional[int] = None
    session_ttl_sec:      Optional[int] = None
    max_context_tokens:   Optional[int] = None


class MasJson(BaseModel):
    agents: List[Dict[str, Any]]


class MasTurn(BaseModel):
    message:        str
    session_id:     str = "default"
    force_cloud:    bool = False
    force_continue: bool = False
    # Optional per-session overrides (only honored when the session is
    # first created — once a session exists, these are sticky on it).
    switch_threshold:     Optional[float] = None
    retrieval_threshold:  Optional[float] = None
    stage1_steps:         Optional[int] = None
    stage2_steps:         Optional[int] = None
    n_reformulations:     Optional[int] = None
    max_new_tokens:       Optional[int] = None
    cloud_continue:       Optional[bool] = None
    prefix_tokens:        Optional[int] = None
    persist:              Optional[bool] = None
    cloud_provider:       Optional[str] = None


class ChatTurn(BaseModel):
    session_id:    str = "default"
    message:       str
    provider:      Optional[str] = None
    recovery_mode: str = "natural"


def _get_or_make_mas_session(body: MasTurn):
    if body.session_id in _mas_sessions:
        return _mas_sessions[body.session_id]
    from agcl.recursive import RecursiveSession
    mas = _build_mas_singleton()
    kwargs: Dict[str, Any] = {}
    for k in ("switch_threshold", "retrieval_threshold", "stage1_steps",
              "stage2_steps", "n_reformulations", "max_new_tokens",
              "cloud_continue", "prefix_tokens", "persist"):
        v = getattr(body, k, None)
        if v is not None:
            kwargs[k] = v
    if body.cloud_provider:
        kwargs["provider"] = body.cloud_provider
    sess = RecursiveSession(mas, verbose=False, **kwargs)
    _mas_sessions[body.session_id] = sess
    return sess


# ============================================================
# Router
# ============================================================

def make_node_router() -> APIRouter:
    r = APIRouter(prefix="/node", tags=["node"])

    # ---- liveness + auth check ----

    @r.get("/health")
    def health():
        return {
            "ok": True, "service": "agcl-node",
            "version": "1",
            "issued_at": _NODE_AUTH.get("issued_at"),
        }

    @r.post("/auth/verify")
    def verify_token(authorization: Optional[str] = Header(None)):
        # Public-allowlisted at the middleware; we do the actual check here.
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer")
        if authorization[len("Bearer "):] != _NODE_AUTH.get("key"):
            raise HTTPException(401, "invalid token")
        return {"ok": True, "issued_at": _NODE_AUTH.get("issued_at")}

    # ---- system info ----

    @r.get("/info")
    def info():
        from agcl import config as cfg
        from agcl.recursive import persistence as P
        # Read keys from live os.environ — the .env file might have
        # been edited (e.g., via Setup → Auto-setup chat) after the
        # node started, so the module-level constants can be stale.
        openai_key = os.environ.get("OPENAI_API_KEY") or cfg.OPENAI_API_KEY
        claude_key = os.environ.get("ANTHROPIC_API_KEY") or cfg.ANTHROPIC_API_KEY
        return {
            "service": "agcl-node",
            "platform": {
                "state_dir": cfg.STATE_DIR,
                "default_cloud": cfg.DEFAULT_CLOUD,
                "providers": {
                    "openai": bool(openai_key),
                    "claude": bool(claude_key),
                },
                "openai_configured": bool(openai_key),
                "claude_configured": bool(claude_key),
                "openai_model": cfg.OPENAI_MODEL,
                "claude_model": cfg.CLAUDE_MODEL,
            },
            "mas": {
                "pattern": cfg.MAS_PATTERN,
                "rounds":  cfg.MAS_ROUNDS,
                "device":  cfg.MAS_DEVICE,
                "dtype":   cfg.MAS_DTYPE,
                "agents":  cfg.MAS_AGENTS,
            },
            "local_model": {
                "path":               cfg.LOCAL_MODEL_PATH,
                "n_ctx":              cfg.LOCAL_N_CTX,
                "n_gpu_layers":       cfg.LOCAL_N_GPU_LAYERS,
                "n_threads":          cfg.LOCAL_N_THREADS,
                "prefix_word_count":  cfg.PREFIX_WORD_COUNT,
            },
            "topics_count": len(P.list_topics(cfg.STATE_DIR)),
        }

    # ---- editable config ----

    @r.get("/config")
    def get_config():
        from agcl import config as cfg
        # Read keys live so post-startup .env edits show up immediately.
        openai_key = os.environ.get("OPENAI_API_KEY") or cfg.OPENAI_API_KEY
        claude_key = os.environ.get("ANTHROPIC_API_KEY") or cfg.ANTHROPIC_API_KEY
        return {
            "cloud": {
                "default":        cfg.DEFAULT_CLOUD,
                "openai_model":   cfg.OPENAI_MODEL,
                "claude_model":   cfg.CLAUDE_MODEL,
                "has_openai_key": bool(openai_key),
                "has_claude_key": bool(claude_key),
            },
            "local_model": {
                "path":               cfg.LOCAL_MODEL_PATH,
                "n_ctx":              cfg.LOCAL_N_CTX,
                "n_gpu_layers":       cfg.LOCAL_N_GPU_LAYERS,
                "n_threads":          cfg.LOCAL_N_THREADS,
                "prefix_word_count":  cfg.PREFIX_WORD_COUNT,
            },
            "mas": {
                "pattern":  cfg.MAS_PATTERN,
                "rounds":   cfg.MAS_ROUNDS,
                "device":   cfg.MAS_DEVICE,
                "dtype":    cfg.MAS_DTYPE,
                "agents":   cfg.MAS_AGENTS,
            },
            "session_defaults": {
                "switch_threshold":    0.6,
                "retrieval_threshold": 0.75,
                "stage1_steps":        30,
                "stage2_steps":        20,
                "n_reformulations":    6,
                "max_new_tokens":      128,
                "cloud_continue":      False,
                "prefix_tokens":       12,
                "persist":             True,
            },
            "storage": {
                "state_dir":          cfg.STATE_DIR,
                "idle_flush_sec":     cfg.IDLE_FLUSH_SEC,
                "session_ttl_sec":    cfg.SESSION_TTL_SEC,
                "max_context_tokens": cfg.MAX_CONTEXT_TOKENS,
            },
        }

    @r.patch("/config")
    def patch_config(p: ConfigPatch):
        env_updates: Dict[str, str] = {}
        m = {
            "default_cloud":      "DEFAULT_CLOUD",
            "openai_model":       "OPENAI_MODEL",
            "claude_model":       "CLAUDE_MODEL",
            "local_model_path":   "LOCAL_MODEL_PATH",
            "local_n_ctx":        "LOCAL_N_CTX",
            "local_n_gpu_layers": "LOCAL_N_GPU_LAYERS",
            "local_n_threads":    "LOCAL_N_THREADS",
            "prefix_word_count":  "PREFIX_WORD_COUNT",
            "mas_pattern":        "MAS_PATTERN",
            "mas_rounds":         "MAS_ROUNDS",
            "mas_device":         "MAS_DEVICE",
            "mas_dtype":          "MAS_DTYPE",
            "idle_flush_sec":     "IDLE_FLUSH_SEC",
            "session_ttl_sec":    "SESSION_TTL_SEC",
            "max_context_tokens": "MAX_CONTEXT_TOKENS",
        }
        for field, env_key in m.items():
            v = getattr(p, field)
            if v is not None:
                env_updates[env_key] = str(v)

        for k, v in env_updates.items():
            os.environ[k] = v
        if env_updates:
            _persist_env(env_updates)

        # Changes that require rebuilding the MAS or restarting llama.cpp.
        restart_keys = {
            "LOCAL_MODEL_PATH", "LOCAL_N_CTX", "LOCAL_N_GPU_LAYERS",
            "LOCAL_N_THREADS",
            "MAS_PATTERN", "MAS_ROUNDS", "MAS_DEVICE", "MAS_DTYPE",
        }
        requires_restart = any(k in restart_keys for k in env_updates)
        return {
            "ok": True,
            "applied": list(env_updates),
            "requires_restart": requires_restart,
        }

    # ---- mas.json ----

    @r.get("/config/mas")
    def get_mas_json():
        from agcl import config as cfg
        p = Path("mas.json")
        if p.exists():
            return {"source": "mas.json",
                    "data": json.loads(p.read_text())}
        return {"source": "default",
                "data": {"agents": cfg.MAS_AGENTS}}

    @r.put("/config/mas")
    def put_mas_json(body: MasJson):
        p = Path("mas.json")
        if p.exists():
            backup = p.with_suffix(".json.bak")
            backup.write_text(p.read_text())
        p.write_text(json.dumps({"agents": body.agents}, indent=2))
        return {
            "ok": True, "path": str(p),
            "agents": len(body.agents), "requires_restart": True,
        }

    # ---- topics ----

    @r.get("/topics")
    def list_topics():
        from agcl import config as cfg
        from agcl.recursive import persistence as P
        return {"topics": P.list_topics(cfg.STATE_DIR)}

    @r.delete("/topics/{topic_id}")
    def del_topic(topic_id: str):
        from agcl import config as cfg
        from agcl.recursive import persistence as P
        if not P.delete_topic(cfg.STATE_DIR, topic_id):
            raise HTTPException(404, "topic not found")
        return {"ok": True}

    # ---- mas runtime: one-shot ----

    @r.post("/mas/run")
    async def mas_run(body: MasTurn):
        # Run the heavy work off-loop so other concurrent requests
        # (config reads, topic listing, /node/health) are never blocked.
        sess = await asyncio.to_thread(_get_or_make_mas_session, body)
        out = await asyncio.to_thread(
            lambda: asyncio.run(sess.turn(
                body.message,
                force_cloud=body.force_cloud,
                force_continue=body.force_continue,
            )),
        )
        return {
            "ok": True,
            "session_id": body.session_id,
            "topic_id": sess.current_topic_id,
            "answer": out,
        }

    # ---- mas runtime: streaming with progress ----

    @r.post("/mas/stream")
    async def mas_stream(body: MasTurn):
        # Important: do NOT build the MAS session before returning the
        # StreamingResponse — model loading on cold start can take a minute
        # and the client needs to see a "start" event immediately so the GUI
        # can show "loading…" instead of a silent dead connection.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def on_progress(ev: dict) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ev)
            except RuntimeError:
                pass

        def _run_turn_in_thread(sess, msg: str, **kw):
            # Each worker thread gets its own asyncio loop so the cloud SDK
            # clients inside sess.turn have something to await on. The
            # outer loop's `call_soon_threadsafe` (in on_progress) still
            # routes events to the SSE consumer correctly.
            return asyncio.run(sess.turn(msg, **kw))

        async def runner():
            try:
                fresh = body.session_id not in _mas_sessions
                if fresh and _mas_singleton["mas"] is None:
                    queue.put_nowait({"event": "model_loading"})
                # Heavy sync work (model load) must not block the event loop —
                # otherwise the "start" SSE event we just yielded sits in a
                # buffer for the entire cold-start duration.
                sess = await asyncio.to_thread(
                    _get_or_make_mas_session, body,
                )
                if fresh:
                    queue.put_nowait({"event": "session_ready",
                                       "session_id": body.session_id})
                # Run the whole turn off-loop so training step events
                # actually flow to the client during sync torch ops.
                out = await asyncio.to_thread(
                    _run_turn_in_thread,
                    sess, body.message,
                    force_cloud=body.force_cloud,
                    force_continue=body.force_continue,
                    on_progress=on_progress,
                )
                queue.put_nowait({"event": "answer",
                                   "text": out,
                                   "topic_id": sess.current_topic_id})
            except Exception as e:
                from agcl.recursive.control import HaltedError
                if isinstance(e, HaltedError):
                    # auto_train already emitted `halted` via the gate; just
                    # close the stream cleanly without an error banner.
                    queue.put_nowait({"event": "halted_done",
                                      "message": str(e)})
                else:
                    queue.put_nowait({"event": "error",
                                       "type": type(e).__name__,
                                       "message": str(e)})
            finally:
                queue.put_nowait(DONE)

        task = asyncio.create_task(runner())

        async def gen():
            yield _sse({"event": "start", "session_id": body.session_id})
            try:
                while True:
                    item = await queue.get()
                    if item is DONE:
                        break
                    yield _sse(item)
                yield _sse({"event": "done"})
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---- mas sessions ----

    @r.get("/mas/sessions")
    def mas_list_sessions():
        return {"sessions": [
            {
                "session_id": sid,
                "topic_id":   sess.current_topic_id,
                "topic_seed": sess.topic_seed,
                "trained":    sess.trained_once,
                "n_turns":    len(sess.history) // 2,
            }
            for sid, sess in _mas_sessions.items()
        ]}

    @r.get("/mas/sessions/{sid}")
    def mas_get_session(sid: str):
        if sid not in _mas_sessions:
            raise HTTPException(404, "session not found")
        s = _mas_sessions[sid]
        return {
            "session_id": sid,
            "topic_id":   s.current_topic_id,
            "topic_seed": s.topic_seed,
            "trained":    s.trained_once,
            "history":    s.history,
        }

    @r.delete("/mas/sessions/{sid}")
    def mas_delete_session(sid: str):
        if sid not in _mas_sessions:
            raise HTTPException(404, "session not found")
        del _mas_sessions[sid]
        return {"ok": True}

    @r.post("/mas/rebuild")
    def mas_rebuild():
        """Drop the loaded MAS singleton + sessions; next /mas/run rebuilds
        from the current config. Useful after changing mas.json / MAS_*."""
        _reset_mas_singleton()
        return {"ok": True}

    # ---- training control: pause / resume / halt / latent / status ----
    # All four endpoints address an existing in-memory session by id. They
    # mutate the session's TrainingControl handle, which the auto_train
    # loop polls between steps. Effects are observed in the SSE stream as
    # `paused`, `resumed`, `halted` events.

    def _require_session(sid: str):
        if sid not in _mas_sessions:
            raise HTTPException(404, "session not found")
        return _mas_sessions[sid]

    @r.post("/mas/sessions/{sid}/pause")
    def mas_pause(sid: str):
        s = _require_session(sid)
        s.control.pause()
        return {"ok": True, "status": s.control.status()}

    @r.post("/mas/sessions/{sid}/resume")
    def mas_resume(sid: str):
        s = _require_session(sid)
        s.control.resume()
        return {"ok": True, "status": s.control.status()}

    @r.post("/mas/sessions/{sid}/halt")
    def mas_halt(sid: str):
        """Halt training mid-run. Raises HaltedError inside the loop; the
        SSE stream emits a `halted` event then `done`."""
        s = _require_session(sid)
        s.control.halt()
        return {"ok": True, "status": s.control.status()}

    @r.get("/mas/sessions/{sid}/status")
    def mas_status(sid: str):
        return {"ok": True, "status": _require_session(sid).control.status()}

    @r.get("/mas/sessions/{sid}/latent")
    def mas_latent(sid: str):
        """Return a snapshot of the most recently observed loop latent.
        Useful for inspecting the latent space mid-training. Returns
        `{snapshot: null}` when no latent has been recorded yet."""
        s = _require_session(sid)
        return {"ok": True, "snapshot": s.control.latent_snapshot()}

    # ---- plugins ----
    # A plugin is a Python file in `plugins/` that exposes a `register(ctx)`
    # function. `ctx` is a small object giving the plugin access to the
    # node router, the MAS singleton accessor, and the session map. The
    # contract is documented in docs/plugins.md.

    @r.get("/plugins")
    def list_plugins():
        from agcl.plugins import (
            loaded as _loaded, commands as _commands,
            declared_configs as _configs, plugin_tools as _tools,
        )
        return {
            "plugins":  _loaded(),
            "commands": [{"name": k, "help": v.get("help", ""),
                           "aliases": v.get("aliases", [])}
                          for k, v in _commands().items()],
            "config":   _configs(),
            "tools":    _tools(),
        }

    @r.post("/plugins/reload")
    def reload_plugins():
        """Re-discover and re-register all plugins without restarting the
        node. Useful while developing a plugin. Existing routes registered
        by previous loads are NOT removed (FastAPI doesn't support that
        cleanly), so add a route once and edit it in place."""
        from agcl import plugins as _plg
        return {"ok": True, "plugins": _plg.load_plugins(r)}

    # ---- generic config exposure --------------------------------------
    # `/node/config` already exposes the curated knob tree. These three
    # routes are the lower-level, fully-generic surface so a GUI or
    # plugin can read/write any env var (whitelisted), get the running
    # config bundle, and inspect runtime info — without us having to
    # extend the curated patch model every time someone adds a knob.

    @r.get("/config/env")
    def get_env(prefix: Optional[str] = None):
        """Return all AGCL-relevant env vars currently in the process.
        Optional `?prefix=AGCL_` filters; secrets are masked."""
        SECRET = ("KEY", "SECRET", "TOKEN", "PASSWORD")
        out: Dict[str, str] = {}
        for k, v in os.environ.items():
            if prefix and not k.startswith(prefix):
                continue
            # Heuristic mask: anything that looks like a secret only shows length.
            if any(s in k for s in SECRET):
                out[k] = f"***({len(v)} chars)" if v else ""
            else:
                out[k] = v
        return {"env": out, "count": len(out)}

    class EnvPatch(BaseModel):
        updates: Dict[str, str]
        persist: bool = True
        allowlist_only: bool = True

    @r.patch("/config/env")
    def patch_env(body: EnvPatch):
        """
        Generic env-var patch. By default only allows keys from the
        bundle allowlist (so nobody can drop arbitrary vars from a
        compromised GUI). Writes to .env when `persist=True`.
        """
        from agcl.config_bundle import _EXPORT_ENV  # noqa: PLC2701
        allow = set(_EXPORT_ENV)
        rejected: List[str] = []
        applied: Dict[str, str] = {}
        for k, v in body.updates.items():
            if body.allowlist_only and k not in allow:
                rejected.append(k)
                continue
            os.environ[k] = str(v)
            applied[k] = str(v)
        if body.persist and applied:
            _persist_env(applied)
        return {"ok": True, "applied": list(applied),
                "rejected": rejected, "persisted": body.persist}

    @r.get("/config/all")
    def config_all():
        """Full config bundle (the same shape as `agcl config export`)."""
        from agcl.config_bundle import export
        # Write to a tmp path that we delete; export() returns the dict.
        import tempfile
        with tempfile.NamedTemporaryFile("w+", delete=True, suffix=".json") as tmp:
            return export(tmp.name)

    @r.post("/config/import")
    async def config_import(request: Request):
        """
        Validate a posted bundle against the local environment. Returns
        a hint report. With `?apply=true`, also writes .env / mas.json.
        """
        from agcl.config_bundle import validate, import_bundle
        body = await request.json()
        apply = request.query_params.get("apply", "").lower() in ("1", "true", "yes")
        if not isinstance(body, dict):
            raise HTTPException(400, "bundle must be a JSON object")
        if apply:
            # Write to disk first so import_bundle can read it back.
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
                tmp.write(json.dumps(body))
                tmp_path = tmp.name
            try:
                return import_bundle(tmp_path, apply=True)
            finally:
                try: os.unlink(tmp_path)
                except OSError: pass
        return {"dry_run": True, **validate(body)}

    @r.get("/runtime/info")
    def runtime_info():
        """Cheap snapshot of the running process — version, threads,
        loaded providers, GPU presence. Lets a GUI render a 'running on'
        badge without making three calls."""
        import platform, sys as _sys
        info: Dict[str, Any] = {
            "python":     _sys.version.split()[0],
            "platform":   platform.platform(),
            "machine":    platform.machine(),
            "pid":        os.getpid(),
            "cwd":        os.getcwd(),
            "env_count":  len(os.environ),
        }
        # Optional GPU detection (cheap — just probes nvidia-smi).
        try:
            import shutil, subprocess
            if shutil.which("nvidia-smi"):
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=name,memory.total",
                     "--format=csv,noheader"],
                    timeout=2, stderr=subprocess.DEVNULL,
                ).decode().strip().splitlines()
                info["gpus"] = [ln.strip() for ln in out if ln.strip()]
        except Exception:
            info["gpus"] = []
        # Toolkit summary
        try:
            from agcl.toolkit import registry as _reg
            info["toolkit"] = _reg.discover()
        except Exception:
            pass
        return info

    # ---- mini-model background trainer --------------------------------
    # Optional, toggleable, pause/resume-able tiny model that trains in
    # the background off latents + reformulations captured during normal
    # AGCL use. See docs/minimodel.md and docs/plugins.md.

    @r.get("/mini/status")
    def mini_status():
        from agcl.mini import runtime as M
        return M.status()

    @r.get("/mini/config")
    def mini_get_config():
        from agcl.mini import runtime as M
        return M.get_trainer().config.to_dict()

    @r.patch("/mini/config")
    async def mini_patch_config(request: Request):
        from agcl.mini import runtime as M
        body = await request.json()
        t = M.get_trainer()
        result = t.config.update(body or {})
        # `lr` change is hot-applicable; rebuild for arch-shaping fields.
        if "lr" in result["applied"]:
            for g in t.opt.param_groups:
                g["lr"] = float(t.config.lr)
        if result.get("requires_rebuild"):
            M.rebuild()
        return {"ok": True, **result}

    @r.post("/mini/start")
    def mini_start():
        from agcl.mini import runtime as M
        t = M.get_trainer()
        t.config.enabled = True
        t.start()
        return {"ok": True, "status": t.status()}

    @r.post("/mini/stop")
    def mini_stop():
        from agcl.mini import runtime as M
        t = M.get_trainer()
        t.stop()
        return {"ok": True, "status": t.status()}

    @r.post("/mini/pause")
    def mini_pause():
        from agcl.mini import runtime as M
        t = M.get_trainer()
        t.pause()
        return {"ok": True, "status": t.status()}

    @r.post("/mini/resume")
    def mini_resume():
        from agcl.mini import runtime as M
        t = M.get_trainer()
        t.resume()
        return {"ok": True, "status": t.status()}

    @r.post("/mini/test")
    async def mini_test(request: Request):
        from agcl.mini import runtime as M
        body = await request.json()
        prompt = (body.get("prompt") or "").strip()
        max_new = int(body.get("max_new", 32))
        temperature = float(body.get("temperature", 0.8))
        if not prompt:
            raise HTTPException(400, "prompt required")
        return M.get_trainer().test(prompt, max_new=max_new, temperature=temperature)

    @r.post("/mini/checkpoint")
    def mini_checkpoint():
        from agcl.mini import runtime as M
        M.get_trainer().force_checkpoint()
        return {"ok": True}

    @r.get("/mini/presets")
    def mini_presets():
        from agcl.mini.strategies import PRESETS
        from agcl.mini.attention import ATTENTION_MASKS
        return {
            "strategies": {k: v for k, v in PRESETS.items()},
            "attention":  list(ATTENTION_MASKS.keys()),
            "archs":      ["transformer", "mlp"],
        }

    # ---- main-agent chat passthrough (delegates to existing /chat route) ----
    # The existing /chat/{session_id} endpoint in main.py already does
    # streaming. We re-expose it under /node/chat/{session_id} so the GUI
    # only has to know one prefix. The implementation forwards to the
    # in-process module to share state.

    @r.post("/chat/{session_id}")
    async def node_chat(session_id: str, body: ChatTurn):
        # Reuse main._run_chat directly — same SSE format as main.py.
        from main import _run_chat
        msg = body.message.strip()
        if not msg:
            raise HTTPException(400, "message required")

        async def gen():
            async for line in _run_chat(
                session_id, msg, body.provider, body.recovery_mode,
            ):
                yield line
        return StreamingResponse(gen(), media_type="text/event-stream")

    @r.get("/sessions")
    def list_chat_sessions():
        from agcl import state as S
        return {"sessions": list(S.session_list())}

    @r.get("/sessions/{sid}")
    def get_chat_session(sid: str):
        from agcl import state as S
        sess = S.get_session(sid)
        return {
            "session_id":    sid,
            "message_count": len(sess["messages"]),
            "messages":      sess["messages"],
            "created_at":    sess.get("created_at"),
            "last_active":   sess.get("last_active"),
        }

    @r.delete("/sessions/{sid}")
    def del_chat_session(sid: str):
        from agcl import state as S
        S.flush_session(sid)
        return {"ok": True, "flushed": sid}

    # ---- usage / quotas / custom providers --------------------------
    # Powers the dashboard at /node/dashboard. Every cloud call
    # (chat-path + knowledge-formation bootstrap) is recorded by
    # agcl.usage with a `kind` tag, so a GUI can split out training
    # cost from regular session cost.

    @r.get("/usage/summary")
    def usage_summary():
        from agcl import usage as U
        snap = U.status_snapshot()
        return {**snap, "sessions": U.all_sessions()}

    @r.get("/usage/providers")
    def usage_providers():
        from agcl import usage as U
        return U.status_snapshot()

    @r.get("/usage/sessions")
    def usage_sessions():
        from agcl import usage as U
        return {"sessions": U.all_sessions()}

    @r.get("/usage/sessions/{sid}")
    def usage_session_detail(sid: str):
        from agcl import usage as U
        return U.session_breakdown(sid)

    @r.get("/usage/timeseries")
    def usage_timeseries(
        session_id: Optional[str] = None,
        provider:   Optional[str] = None,
        kind:       Optional[str] = None,
        bucket_sec: int = 60,
        lookback_sec: int = 3600,
    ):
        from agcl import usage as U
        return {"buckets": U.timeseries(
            session_id=session_id, provider=provider, kind=kind,
            bucket_sec=bucket_sec, lookback_sec=lookback_sec,
        ), "bucket_sec": bucket_sec, "lookback_sec": lookback_sec}

    class QuotaPatch(BaseModel):
        max_tokens:     Optional[int]   = None
        max_credit_usd: Optional[float] = None
        period:         str             = "lifetime"
        notes:          str             = ""

    @r.put("/usage/quota/{provider}")
    def set_quota(provider: str, body: QuotaPatch):
        from agcl import usage as U
        q = U.set_quota(
            provider,
            max_tokens=body.max_tokens,
            max_credit_usd=body.max_credit_usd,
            period=body.period,
            notes=body.notes,
        )
        return {"ok": True, "quota": q.to_dict()}

    @r.delete("/usage/quota/{provider}")
    def delete_quota(provider: str):
        from agcl import usage as U
        return {"ok": U.clear_quota(provider)}

    @r.get("/usage/quotas")
    def get_quotas():
        from agcl import usage as U
        return {"quotas": U.all_quotas()}

    class CustomProvider(BaseModel):
        name:        str
        base_url:    str
        api_key_env: str
        model:       str
        kind:        str = "openai-compatible"
        notes:       str = ""

    @r.post("/usage/providers/custom")
    def register_provider(body: CustomProvider):
        from agcl import usage as U
        return U.register_custom_provider(
            body.name, base_url=body.base_url,
            api_key_env=body.api_key_env, model=body.model,
            kind=body.kind, notes=body.notes,
        )

    @r.get("/usage/providers/custom")
    def list_custom_providers():
        from agcl import usage as U
        return {"providers": U.list_custom_providers()}

    @r.delete("/usage/providers/custom/{name}")
    def delete_custom_provider(name: str):
        from agcl import usage as U
        return {"ok": U.unregister_custom_provider(name)}

    # ---- dashboard (static SPA) -------------------------------------
    # Single-file HTML+JS+inline-SVG. Lives next to the app so it
    # benefits from the same auth gate. Browser-side passes the bearer
    # via a paste-once form, then talks to the same /node/* endpoints.

    @r.get("/dashboard", response_class=None)
    def dashboard():
        from fastapi.responses import HTMLResponse
        from agcl.dashboard import HTML
        return HTMLResponse(HTML)

    return r


# ============================================================
# App builder
# ============================================================

def build_node_app(auth_key: Optional[str] = None,
                    cors_origins: Optional[List[str]] = None) -> FastAPI:
    """
    Wraps the main agcl FastAPI app with auth + CORS + node routes.
    Returns a FastAPI app ready to be passed to uvicorn.

    Order of middleware registration matters: auth is added FIRST so it
    becomes innermost, CORS is added LAST so it becomes outermost. That way
    OPTIONS preflight is handled by CORS without ever hitting auth, and
    auth-rejection responses still get CORS headers attached on the way out.
    """
    issue_key(custom_key=auth_key)

    # Late import — building the app touches torch/transformers/etc.
    from main import app as base_app

    # Auth FIRST (innermost), CORS LAST (outermost).
    base_app.middleware("http")(_auth_middleware)
    base_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Type"],
    )
    router = make_node_router()
    # Plugins register additional routes on the same APIRouter so they live
    # under /node/* alongside the built-ins. Failures in one plugin must
    # not break the others — load_plugins handles that internally.
    try:
        from agcl import plugins as _plg
        _plg.load_plugins(router)
    except Exception:
        pass
    base_app.include_router(router)

    # Toolkit router (/node/toolkit/*) — adapters for LiteLLM, Ollama,
    # vLLM, Redis, S3, Cloudflare, Docker/K8s manifest emitters, WebRTC
    # signaling. Pulled in as a sibling so `/node` stays the single
    # auth-gated prefix.
    try:
        from agcl.toolkit.endpoints import make_toolkit_router
        base_app.include_router(make_toolkit_router())
    except Exception as e:
        print(f"[node] toolkit router unavailable: {type(e).__name__}: {e}")

    # Setup router (/node/setup/*) — autoconfig wizard, chat quick-setup,
    # HF + GGUF downloaders, model registry, job manager.
    try:
        from agcl.setup_endpoints import make_setup_router
        base_app.include_router(make_setup_router())
    except Exception as e:
        print(f"[node] setup router unavailable: {type(e).__name__}: {e}")

    return base_app
