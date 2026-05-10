"""
AGCL - Agentic CLI. Entry point.

Run with:
  python main.py                         # persistent TUI shell (default)
  python main.py shell                   # same as default
  python main.py chat                    # legacy chat client (auto-starts server)
  python main.py serve --port 8000       # run the FastAPI server in the foreground
  python main.py node --port 9876        # expose this PC to a GUI client
  python main.py recursive run           # recursive multi-agent reasoning
  python main.py mini status             # background mini-model trainer
  python main.py mcp                     # MCP server (Claude/Cursor/OpenAI/...)
  python main.py slack                   # Slack Bolt adapter
  python main.py agentmod                # OpenAgents AgentMod
  python main.py openapi                 # dump OpenAPI 3.0 spec
  uvicorn main:app --reload --port 8000  # equivalent to `serve`

Endpoints:
  POST /chat/{session_id}          streaming chat (SSE)
  GET  /session/{session_id}       view session messages
  DELETE /session/{session_id}     flush + clear session
  GET  /pressure                   current API pressure stats
  GET  /patterns                   learned active hours
  GET  /health                     idle time, pressure, session count
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

import agcl.state as state
import agcl.pressure as prs
import agcl.patterns as patterns
import agcl.context as context
from agcl.local_llm import generate_prefix, unload as unload_local_model
from agcl.cloud import stream_continuation
from agcl.config import IDLE_FLUSH_SEC

DEFAULT_PORT = 8000

#  startup
@asynccontextmanager
async def lifespan(app):
    patterns.load()
    patterns.start_scheduler()

    def on_active_hour(hour, hits):
        print(f"[patterns] active hour {hour}:00 — {hits} historical hits")
    patterns.register_trigger(on_active_hour)

    threading.Thread(target=_idle_watcher, daemon=True).start()
    yield
    state.flush_all()   # clean shutdown

app = FastAPI(title="nano-cloud-agent", lifespan=lifespan)

def _idle_watcher():
    """
    Background loop: when quiet long enough, flush all sessions to disk
    and unload the local model to reclaim RAM.
    """
    while True:
        time.sleep(30)
        if state.is_idle():
            state.flush_all()
            evicted = state.evict_idle_sessions()
            if evicted:
                print(f"[idle] evicted {evicted} sessions to disk")
            unload_local_model()


#  shared response builder

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"

async def _run_chat(session_id, user_message, provider, recovery_mode):
    """
    Core logic extracted so it's reusable by any route that needs chat.
    Yields raw SSE strings.
    """
    state.touch()
    patterns.record_usage()

    sess     = state.get_session(session_id)
    messages = sess["messages"]

    # append user turn + recontextualize if needed
    messages = await context.append(messages, "user", user_message)
    sess["messages"] = messages
    state.put_session(session_id, sess)

    # local prefix — immediate
    prefix, local_ms = generate_prefix(messages)
    local_ms_int = int(local_ms * 1000)

    if prefix:
        yield _sse({"type": "prefix", "text": prefix, "local_ms": local_ms_int})

    # cloud continuation
    full_response = prefix
    async for chunk in stream_continuation(messages, prefix, provider, recovery_mode):
        full_response += chunk
        yield _sse({"type": "chunk", "text": chunk})

    # save full assistant turn
    sess["messages"] = await context.append(sess["messages"], "assistant", full_response)
    state.put_session(session_id, sess)
    state.flush_session(session_id)

    yield _sse({"type": "done", "pressure": prs.pressure()})


#  routes

@app.post("/chat/{session_id}")
async def chat(session_id: str, request: Request):
    body          = await request.json()
    user_message  = body.get("message", "").strip()
    provider      = body.get("provider", None)        # "openai" | "claude" | None (uses default)
    recovery_mode = body.get("recovery_mode", "natural")

    if not user_message:
        raise HTTPException(400, "message required")

    return StreamingResponse(
        _run_chat(session_id, user_message, provider, recovery_mode),
        media_type="text/event-stream",
    )

@app.get("/session/{session_id}")
def get_session(session_id: str):
    sess = state.get_session(session_id)
    return {
        "session_id":  session_id,
        "message_count": len(sess["messages"]),
        "messages":    sess["messages"],
        "created_at":  sess.get("created_at"),
        "last_active": sess.get("last_active"),
    }

@app.delete("/session/{session_id}")
def delete_session(session_id: str):
    state.flush_session(session_id)
    return {"flushed": session_id}

@app.get("/pressure")
def get_pressure():
    return prs.pressure()

@app.get("/patterns")
def get_patterns():
    return {
        "active_hours":          patterns.active_hours(),
        "upcoming_active_hours": patterns.upcoming_active_hours(),
    }

@app.get("/health")
def health():
    return {
        "idle_sec":      round(state.idle_seconds()),
        "is_idle":       state.is_idle(),
        "sessions_live": len(state.session_list()),
        "pressure":      prs.pressure(),
    }


#  CLI client

def _stream_chat(base, session_id, message, provider, recovery):
    url  = f"{base}/chat/{session_id}"
    data = {"message": message, "provider": provider, "recovery_mode": recovery}

    with httpx.stream("POST", url, json=data, timeout=60) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])

            t = payload.get("type")
            if t == "prefix":
                ms  = payload.get("local_ms", 0)
                txt = payload.get("text", "")
                print(f"\033[90m[local {ms}ms]\033[0m ", end="", flush=True)
                print(txt, end="", flush=True)
            elif t == "chunk":
                print(payload.get("text", ""), end="", flush=True)
            elif t == "done":
                p = payload.get("pressure", {})
                print(f"\n\033[90m[pressure rate={p.get('rate_ratio')} lat={p.get('avg_latency_sec')}s]\033[0m")

def _ensure_server(base, port):
    try:
        httpx.get(f"{base}/health", timeout=2)
        return  # already up
    except httpx.ConnectError:
        pass

    print("[server] not running, starting...")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # wait until it responds, bail after 15s
    for _ in range(30):
        time.sleep(0.5)
        try:
            httpx.get(f"{base}/health", timeout=1)
            print("[server] ready")
            return
        except httpx.ConnectError:
            pass
    print(f"[server] failed to start — run manually: python main.py serve --port {port}")
    sys.exit(1)

def _run_cli(args):
    base = f"http://localhost:{args.port}"
    _ensure_server(base, args.port)
    print(f"Agent CLI — session={args.session}  (ctrl+c to quit)\n")
    while True:
        try:
            msg = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not msg:
            continue
        print("agent: ", end="", flush=True)
        _stream_chat(base, args.session, msg, args.provider, args.recovery)

def _run_serve(args):
    import uvicorn
    uvicorn.run("main:app", host=args.host, port=args.port, reload=args.reload, log_level=args.log_level)


def _run_node(args):
    """
    Open this PC's agcl install to an authorized GUI client. Generates
    (or reuses) a bearer auth key and starts the node FastAPI server.

    The user runs this on their own computer:
        python main.py node --port 9876
    and pastes the printed key into a remote GUI on the same machine.
    """
    import uvicorn
    from agcl.node import build_node_app, current_key

    custom = args.auth_key or os.environ.get("AGCL_NODE_AUTH") or None
    cors = [o.strip() for o in args.cors.split(",") if o.strip()] or ["*"]

    # Build the app once so the key is fixed before we hand off to uvicorn.
    build_node_app(auth_key=custom, cors_origins=cors)

    print()
    print("=" * 60)
    print("  AGCL node - ready")
    print("=" * 60)
    print(f"  bind:        {args.bind}:{args.port}")
    print(f"  cors:        {', '.join(cors)}")
    print(f"  auth key:    {current_key()}")
    print()
    print("  paste the auth key above into your GUI to authorize this PC.")
    print("  every request must send:  Authorization: Bearer <key>")
    print("  killing this process invalidates the key.")
    print("=" * 60)
    print()

    uvicorn.run(
        "main:app",
        host=args.bind, port=args.port,
        log_level=args.log_level,
    )


def _run_recursive(args):
    """Recursive-MAS feature: validate, inspect config, or run a prompt."""
    from agcl.recursive import validate as rv

    if args.action == "validate":
        ok = rv.run_all()
        sys.exit(0 if ok else 1)

    if args.action == "info":
        from agcl import config as cfg
        from agcl.recursive import persistence as rp
        print("agcl — RecursiveMAS modules:")
        print("  InnerLink, OuterLink              — projection links")
        print("  HFBackend, GGUFBackend            — model backends")
        print("  RecursiveAgent, RecursiveMAS      — agent wrapper + loop controller")
        print("  RecursiveSession                  — multi-turn driver")
        print("  auto_train, TopicTracker          — cloud-teacher online training")
        print("  persistence.{save,load,find}_topic— per-topic state + similarity index")
        print("  build_from_config / build_mas_from_specs / build_agent")
        print()
        print("config:")
        print(f"  MAS_PATTERN = {cfg.MAS_PATTERN}")
        print(f"  MAS_ROUNDS  = {cfg.MAS_ROUNDS}")
        print(f"  MAS_DEVICE  = {cfg.MAS_DEVICE}")
        print(f"  MAS_DTYPE   = {cfg.MAS_DTYPE}")
        print(f"  STATE_DIR   = {cfg.STATE_DIR}")
        print(f"  MAS_AGENTS  ({len(cfg.MAS_AGENTS)}):")
        for i, s in enumerate(cfg.MAS_AGENTS):
            print(f"    [{i}] backend={s.get('backend')}  role={s.get('role','')}  model={s.get('model','')}")
        topics = rp.list_topics(cfg.STATE_DIR)
        print(f"\nsaved topics ({len(topics)}):")
        for t in topics[:10]:
            print(f"  {t['topic_id']}  dims={t.get('signature',{}).get('dims')}  "
                  f"seed={t.get('seed_question','')[:60]!r}")
        if len(topics) > 10:
            print(f"  ... +{len(topics) - 10} more")
        print()
        print("commands:")
        print("  python main.py --config              interactive configurator (assisted)")
        print("  python main.py --autoconfig          one-shot canonical setup")
        print("  python main.py recursive validate    21 unit checks")
        print("  python main.py recursive run         interactive multi-turn")
        print("  python main.py recursive run \"...\"   one-shot")
        return

    if args.action == "run":
        from agcl.recursive import build_from_config, RecursiveSession

        print("[agcl] building MAS from config...")
        mas = build_from_config()
        print(f"[agcl] {len(mas.agents)} agents, {mas.n_rounds} rounds, dims={mas.dims}")

        sess = RecursiveSession(
            mas,
            provider=args.cloud_provider,
            stage1_steps=args.stage1_steps,
            stage2_steps=args.stage2_steps,
            switch_threshold=args.switch_threshold,
            retrieval_threshold=args.retrieval_threshold,
            n_reformulations=args.n_reformulations,
            max_new_tokens=args.max_new_tokens,
            cloud_continue=args.continue_with_cloud,
            prefix_tokens=args.prefix_tokens,
            persist=not args.no_persist,
            strict=args.strict,
        )

        # one-shot mode: prompt was passed on the command line
        if args.prompt:
            out = asyncio.run(sess.turn(
                args.prompt,
                force_cloud=args.cloud,
                force_continue=args.continue_with_cloud,
            ))
            print()
            print(out)
            return

        # interactive mode
        cont_state = "ON" if args.continue_with_cloud else "off"
        persist_state = "off" if args.no_persist else "ON"
        print(
            "\n[agcl] interactive mode\n"
            "  first turn (or new topic) bootstraps training from cloud, then\n"
            "  follow-ups run locally with cloud auto-fallback on degenerate output.\n"
            "  topics are indexed and reused when similar questions return.\n"
            f"  cloud continuator: {cont_state}    persistence: {persist_state}\n"
            "  directives:  /cloud <msg>     force cloud only\n"
            "               /continue <msg>  local prefix + cloud continuator (one turn)\n"
            "               /topics          list saved topics\n"
            "               /quit            exit\n"
        )
        while True:
            try:
                msg = input("you: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye"); break
            if not msg:
                continue
            if msg == "/quit":
                print("bye"); break
            if msg == "/topics":
                _print_topics(sess.state_dir)
                continue
            force_cloud = False
            force_continue = False
            if msg.startswith("/cloud "):
                force_cloud = True
                msg = msg[len("/cloud "):].strip()
                if not msg: continue
            elif msg == "/cloud":
                msg = _next_line("cloud you: ")
                if not msg: continue
                force_cloud = True
            elif msg.startswith("/continue "):
                force_continue = True
                msg = msg[len("/continue "):].strip()
                if not msg: continue
            elif msg == "/continue":
                msg = _next_line("continue you: ")
                if not msg: continue
                force_continue = True
            try:
                out = asyncio.run(sess.turn(
                    msg, force_cloud=force_cloud, force_continue=force_continue,
                ))
            except Exception as e:
                print(f"[agcl] error: {type(e).__name__}: {e}")
                continue
            print(f"agent: {out}\n")
        return


def _next_line(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _print_topics(state_dir: str) -> None:
    from agcl.recursive import persistence as P
    rows = P.list_topics(state_dir)
    if not rows:
        print("  (no saved topics)")
        return
    for r in rows:
        print(f"  {r['topic_id']}  dims={r.get('signature',{}).get('dims')}  "
              f"seed={r.get('seed_question','')[:60]!r}")


def main():
    ap = argparse.ArgumentParser(prog="agcl")
    sub = ap.add_subparsers(dest="cmd")

    # `serve` runs the FastAPI app via uvicorn
    sp = sub.add_parser("serve", help="run the FastAPI server")
    sp.add_argument("--host",      default="127.0.0.1")
    sp.add_argument("--port",      default=DEFAULT_PORT, type=int)
    sp.add_argument("--reload",    action="store_true")
    sp.add_argument("--log-level", default="info")

    # `chat` opens the legacy chat client (auto-starts server). The TUI
    # shell is the new default; this stays for backward compatibility.
    cp = sub.add_parser("chat", help="legacy chat client (auto-starts server)")
    cp.add_argument("--session",  default="default")
    cp.add_argument("--provider", default=None, choices=["openai", "claude"])
    cp.add_argument("--recovery", default="natural", choices=["natural", "humor", "explicit"])
    cp.add_argument("--port",     default=DEFAULT_PORT, type=int)

    # `shell` opens the persistent TUI (default action when no subcommand)
    sub.add_parser("shell", help="persistent TUI shell with arrow-key menus (default)")

    # `recursive` exposes the recursive-MAS feature
    rp = sub.add_parser("recursive", help="recursive multi-agent system feature")
    rp.add_argument("action", choices=["validate", "info", "run"], nargs="?", default="validate")
    rp.add_argument("prompt", nargs="?", default=None,
                    help="prompt for `recursive run` (omit for interactive mode)")
    rp.add_argument("--max-new-tokens", type=int, default=128)
    rp.add_argument("--cloud", action="store_true",
                    help="force this turn through the cloud model "
                         "(skips local MAS); only used in one-shot mode")
    rp.add_argument("--cloud-provider", default=None,
                    choices=["openai", "claude"],
                    help="override DEFAULT_CLOUD for this session")
    rp.add_argument("--stage1-steps", type=int, default=30,
                    help="cloud-teacher stage A (latent alignment) step count")
    rp.add_argument("--stage2-steps", type=int, default=20,
                    help="cloud-teacher stage B (token CE) step count "
                         "(0 to disable; auto-skipped if final agent is GGUF)")
    rp.add_argument("--switch-threshold", type=float, default=0.6,
                    help="cosine sim below this triggers a topic-switch check")
    rp.add_argument("--retrieval-threshold", type=float, default=0.75,
                    help="cosine sim above this reuses a saved topic's "
                         "trained links instead of retraining")
    rp.add_argument("--n-reformulations", type=int, default=6,
                    help="how many question rephrasings to ask cloud for")
    rp.add_argument("--continue-with-cloud", action="store_true",
                    help="local MAS produces a short prefix; cloud finishes the "
                         "turn (mirrors the main agent's local->cloud handoff)")
    rp.add_argument("--prefix-tokens", type=int, default=12,
                    help="how many tokens the local MAS produces in continuator mode")
    rp.add_argument("--no-persist", action="store_true",
                    help="do not save trained links / centroids to disk")
    rp.add_argument("--strict", action="store_true",
                    help="force the continuator path on every turn — local "
                         "MAS only generates a short prefix, cloud always "
                         "finishes (= AGCL_STRICT=1; reduces small-model hallucination)")

    # `node` opens this PC to an authorized GUI client over HTTP+SSE
    np = sub.add_parser("node",
        help="open this PC to an authorized GUI client (bearer auth + CORS)")
    np.add_argument("--bind",      default="127.0.0.1",
        help="interface to bind (default 127.0.0.1; use 0.0.0.0 to expose)")
    np.add_argument("--port",      default=9876, type=int,
        help="port to listen on (default 9876; editable)")
    np.add_argument("--auth-key",  default=None,
        help="reuse a specific auth key (overrides AGCL_NODE_AUTH)")
    np.add_argument("--cors",      default="*",
        help="comma-separated CORS origins; default '*' (browser-friendly)")
    np.add_argument("--log-level", default="warning",
        help="uvicorn log level (info|warning|error)")

    # `mcp` runs an MCP server speaking JSON-RPC over stdio (default) or HTTP SSE
    mcp_p = sub.add_parser("mcp",
        help="run an MCP server (Model Context Protocol) for AGCL")
    mcp_p.add_argument("--transport", default="stdio",
        choices=["stdio", "sse", "manifest"],
        help="stdio (default), sse (HTTP), or manifest (dump JSON and exit)")
    mcp_p.add_argument("--host", default="127.0.0.1")
    mcp_p.add_argument("--port", default=8765, type=int)
    mcp_p.add_argument("--fast", action="store_true",
        help="use FastMCP (pip install fastmcp) instead of the plain mcp SDK")

    # `slack` runs the Slack Bolt adapter
    sl_p = sub.add_parser("slack",
        help="run the Slack Bolt adapter (Slack AI Apps)")
    sl_p.add_argument("--http", action="store_true",
        help="run in HTTP mode (default: Socket Mode if SLACK_APP_TOKEN set)")
    sl_p.add_argument("--port", default=3000, type=int)

    # `agentmod` runs the OpenAgents AgentMod adapter
    oa_p = sub.add_parser("agentmod",
        help="run the OpenAgents AgentMod adapter")
    oa_p.add_argument("--workspace", default=None,
        help="OpenAgents workspace id to join (omit to just register the mod)")

    # `openapi` dumps the FastAPI-generated OpenAPI spec
    oapi_p = sub.add_parser("openapi",
        help="dump AGCL's OpenAPI 3.0 spec (for Zapier / Copilot Studio / Vertex AI)")
    oapi_p.add_argument("--out", default=None,
        help="write to a file instead of stdout")

    # `run` is the headless task runner — what Multica / Cloud Run / GitHub
    # Actions / shell scripts call to drive AGCL as an orchestratable CLI.
    run_p = sub.add_parser("run",
        help="execute one task with structured output (jsonl|sse|text)")
    run_p.add_argument("--task", required=True,
        help="task description (the issue body / prompt)")
    run_p.add_argument("--workdir", default=None,
        help="working directory; STATE_DIR resolves under <workdir>/.agcl")
    run_p.add_argument("--output", default="jsonl",
        choices=["jsonl", "sse", "text"])
    run_p.add_argument("--session-id", default="default")
    run_p.add_argument("--provider", default=None, choices=["openai", "claude"])
    run_p.add_argument("--cloud", action="store_true",
        help="force cloud-only for this turn (skips local MAS)")
    run_p.add_argument("--continue-with-cloud", action="store_true",
        help="local prefix + cloud finishes (one-shot continuator mode)")
    run_p.add_argument("--skill", action="append", default=None,
        help="path to a markdown skill bundle; repeatable; prepended as context")
    run_p.add_argument("--max-new-tokens", type=int, default=256)
    run_p.add_argument("--resume", default=None,
        help="resume a previous session by id (alias for --session-id)")

    # `orchestrate` positions AGCL as a *local orchestrator* for self-hosted
    # external sites: drive a remote AGCL node from this CLI, run a series
    # of tasks, fan out to multiple endpoints. Reads a YAML/JSON playbook
    # OR a single --task with --target <url>.
    orch_p = sub.add_parser("orchestrate",
        help="drive remote AGCL nodes / self-hosted sites as an orchestrator")
    orch_p.add_argument("playbook", nargs="?", default=None,
        help="path to a playbook (.json or .yaml). omit for --task one-shot")
    orch_p.add_argument("--target", default=None,
        help="single target URL: http://host:9876 or wss://...")
    orch_p.add_argument("--auth-key", default=None,
        help="bearer token for the target (or AGCL_TARGET_KEY env)")
    orch_p.add_argument("--task", default=None,
        help="single task; if omitted, requires a playbook")
    orch_p.add_argument("--output", default="jsonl",
        choices=["jsonl", "text"])
    orch_p.add_argument("--parallel", type=int, default=1,
        help="max concurrent targets when fanning out")

    # `config` import / export / hint — centralised knob bundle for sharing
    cfg_p = sub.add_parser("config",
        help="export / import the AGCL config bundle (env + mas.json)")
    cfg_p.add_argument("action", choices=["export", "import", "hint", "show"],
        nargs="?", default="show")
    cfg_p.add_argument("path", nargs="?", default=None,
        help="path to the bundle file (defaults: agcl-config.json)")
    cfg_p.add_argument("--apply", action="store_true",
        help="actually write changes (without --apply, import only validates)")

    # `toolkit` controls the infra/inference adapters subsystem
    tk_p = sub.add_parser("toolkit",
        help="infra/inference toolkit (litellm, ollama, vllm, redis, s3, cloudflare, docker, k8s, ...)")
    tk_p.add_argument("action", choices=[
        "discover", "ping", "chat",
        "emit-docker", "emit-k8s", "emit-gcp",
        "discord",
    ], default="discover", nargs="?")
    tk_p.add_argument("target", nargs="?", default=None,
        help="`ping <adapter>` or `chat <prompt>`; ignored otherwise")
    tk_p.add_argument("--out", default=None,
        help="output dir for emit-docker / emit-k8s")
    tk_p.add_argument("--gpu", action="store_true",
        help="emit GPU variant of Dockerfile / Compose")
    tk_p.add_argument("--model", default=None,
        help="model alias for `chat` (defaults to AGCL_LLM_MODEL or 'smart')")
    tk_p.add_argument("--stream", action="store_true",
        help="stream chat output token-by-token")

    # `mini` controls the optional background mini-model trainer
    mp = sub.add_parser("mini", help="background mini-model trainer (toggleable)")
    mp.add_argument("action", choices=[
        "status", "start", "stop", "pause", "resume",
        "test", "config", "presets", "checkpoint",
    ], default="status", nargs="?")
    mp.add_argument("prompt", nargs="?", default=None,
                    help="prompt for `mini test` (required for that action)")
    mp.add_argument("--max-new", type=int, default=32)
    mp.add_argument("--temperature", type=float, default=0.8)

    # `autoconfig` sets up a fresh checkout for HF RecursiveMAS
    ap_auto = sub.add_parser("autoconfig",
        help="set up project files for HF RecursiveMAS (idempotent)")
    ap_auto.add_argument("--force", action="store_true",
        help="overwrite mas.json even if it exists")
    ap_auto.add_argument("--install-deps", action="store_true",
        help="pip install transformers if missing")
    ap_auto.add_argument("--download", action="store_true",
        help="pre-download the canonical HF models (~3 GB)")

    # also accept chat flags at the top level so `python main.py --session x` still works
    ap.add_argument("--session",  default="default")
    ap.add_argument("--provider", default=None, choices=["openai", "claude"])
    ap.add_argument("--recovery", default="natural", choices=["natural", "humor", "explicit"])
    ap.add_argument("--port",     default=DEFAULT_PORT, type=int)
    # top-level shortcut so `python main.py --autoconfig` works without a subcommand
    ap.add_argument("--autoconfig", action="store_true",
        help="run autoconfig and exit (equivalent to: autoconfig)")
    ap.add_argument("--config", action="store_true",
        help="open the interactive RecursiveMAS configurator")

    args = ap.parse_args()

    if args.config:
        from agcl.configurator import run as run_configurator
        sys.exit(run_configurator())

    if args.autoconfig:
        from agcl.autoconfig import run as run_autoconfig
        sys.exit(run_autoconfig())

    if args.cmd == "serve":
        _run_serve(args)
    elif args.cmd == "node":
        _run_node(args)
    elif args.cmd == "recursive":
        _run_recursive(args)
    elif args.cmd == "autoconfig":
        from agcl.autoconfig import run as run_autoconfig
        sys.exit(run_autoconfig(
            force=args.force, install_deps=args.install_deps, download=args.download,
        ))
    elif args.cmd == "chat":
        _run_cli(args)
    elif args.cmd == "mini":
        _run_mini(args)
    elif args.cmd == "mcp":
        if args.fast:
            from agcl.integrations import fast_mcp_server as F
            if args.transport == "manifest":
                from agcl.integrations import mcp_server as M
                sys.exit(M.dump_manifest())
            if args.transport == "sse":
                sys.exit(F.run_sse(host=args.host, port=args.port))
            sys.exit(F.run_stdio())
        from agcl.integrations import mcp_server as M
        if args.transport == "manifest":
            sys.exit(M.dump_manifest())
        if args.transport == "sse":
            sys.exit(M.run_sse(host=args.host, port=args.port))
        sys.exit(M.run_stdio())
    elif args.cmd == "slack":
        from agcl.integrations import slack_bolt as S
        sys.exit(S.run(http=args.http, port=args.port))
    elif args.cmd == "agentmod":
        from agcl.integrations import openagents as O
        sys.exit(O.run(workspace=args.workspace))
    elif args.cmd == "openapi":
        from agcl.integrations import openapi_export as OA
        sys.exit(OA.export(out=args.out))
    elif args.cmd == "toolkit":
        sys.exit(_run_toolkit(args))
    elif args.cmd == "run":
        from agcl.runner import run as run_task
        sid = args.resume or args.session_id
        sys.exit(run_task(
            args.task,
            session_id=sid, workdir=args.workdir, output=args.output,
            provider=args.provider, force_cloud=args.cloud,
            force_continue=args.continue_with_cloud,
            skill_files=args.skill,
            max_new_tokens=args.max_new_tokens,
        ))
    elif args.cmd == "orchestrate":
        from agcl.orchestrator import run as orchestrate
        sys.exit(orchestrate(
            playbook=args.playbook, target=args.target,
            auth_key=args.auth_key, task=args.task,
            output=args.output, parallel=args.parallel,
        ))
    elif args.cmd == "config":
        from agcl.config_bundle import cli as run_config_bundle
        sys.exit(run_config_bundle(
            action=args.action, path=args.path, apply=args.apply,
        ))
    else:
        # default: persistent TUI shell. Server is opt-in via "serve"/"node"
        # subcommands or the in-shell menu.
        from agcl.tui import run as run_shell
        sys.exit(run_shell())


def _run_mini(args):
    """One-shot CLI control for the background mini-model trainer."""
    from agcl.mini import runtime as M
    t = M.get_trainer()
    if args.action == "status":
        for k, v in t.status().items():
            print(f"  {k:<14} {v}")
        return
    if args.action == "start":
        t.config.enabled = True
        t.start()
        print("  mini trainer started")
        return
    if args.action == "stop":
        t.stop(); print("  mini trainer stopped"); return
    if args.action == "pause":
        t.pause(); print("  paused"); return
    if args.action == "resume":
        t.resume(); print("  resumed"); return
    if args.action == "checkpoint":
        t.force_checkpoint(); print("  checkpoint saved"); return
    if args.action == "config":
        for k, v in t.config.to_dict().items():
            print(f"  {k:<14} {v}")
        return
    if args.action == "presets":
        from agcl.mini.strategies import PRESETS
        from agcl.mini.attention import ATTENTION_MASKS
        print("  strategies:")
        for k, comp in PRESETS.items():
            print(f"    {k:<18} {comp}")
        print("  attention:", list(ATTENTION_MASKS.keys()))
        print("  archs: transformer | mlp")
        return
    if args.action == "test":
        if not args.prompt:
            print("  usage: python main.py mini test \"<prompt>\"")
            sys.exit(1)
        r = t.test(args.prompt, max_new=args.max_new, temperature=args.temperature)
        print(f"  step={r['step']} arch={r['arch']} strategy={r['strategy']}")
        print(f"  decoded > {r['decoded']!r}")
        return


def _run_toolkit(args) -> int:
    """
    `python main.py toolkit ...`

    Drive the infra/inference adapter layer from the CLI:

        toolkit discover                  # what's configured + importable
        toolkit ping ollama|vllm|...      # reachability check
        toolkit chat "<prompt>"           # send through gateway
        toolkit emit-docker [--gpu] [--out deploy]
        toolkit emit-k8s                  # write Helm chart + manifests
        toolkit discord                   # run the Discord bot adapter
    """
    from agcl.toolkit import registry
    action = args.action

    if action == "discover":
        adapters = registry.discover()
        # Two-column table: name + status. No emojis.
        for name, info in adapters.items():
            cfg = "configured" if info["configured"] else "off"
            imp = "ok" if info["importable"] else "missing"
            print(f"  {name:<12} {cfg:<12} import:{imp}")
            if info.get("import_err"):
                print(f"               -> {info['import_err']}")
        return 0

    if action == "ping":
        target = args.target or ""
        if not target:
            print("usage: python main.py toolkit ping <adapter>", file=sys.stderr)
            return 2
        return _ping_adapter(target)

    if action == "chat":
        prompt = args.target or ""
        if not prompt:
            print("usage: python main.py toolkit chat \"<prompt>\"", file=sys.stderr)
            return 2
        return _toolkit_chat(prompt, model=args.model, stream=args.stream)

    if action == "emit-docker":
        from agcl.toolkit import docker as D
        out = args.out or "deploy"
        result = D.write_to(out)
        # Override compose to honor --gpu flag.
        compose_path = f"{out}/docker-compose.yml"
        with open(compose_path, "w") as f:
            f.write(D.compose(gpu=args.gpu))
        for f in result["files"]:
            print(f"  wrote {f}")
        return 0

    if action == "emit-k8s":
        from agcl.toolkit import k8s as K
        out = args.out or "deploy/agcl-chart"
        chart = K.write_chart(out)
        manifests = K.write_manifests(out + "/manifests")
        for f in chart["files"] + manifests["files"]:
            print(f"  wrote {f}")
        return 0

    if action == "emit-gcp":
        from agcl.toolkit import gcp as G
        out = args.out or "deploy/gcp"
        result = G.write_to(
            out_dir=out,
            project=os.getenv("GOOGLE_CLOUD_PROJECT", "PROJECT_ID"),
            service=os.getenv("AGCL_GCP_SERVICE", "agcl-node"),
            region=os.getenv("AGCL_GCP_REGION",  "us-central1"),
        )
        for f in result["files"]:
            print(f"  wrote {f}")
        print(f"  deploy:  {result['deploy_command']}")
        return 0

    if action == "discord":
        from agcl.toolkit import discord as D
        return D.run()

    print(f"unknown toolkit action: {action!r}", file=sys.stderr)
    return 2


def _ping_adapter(name: str) -> int:
    import asyncio as _aio
    from agcl.toolkit import (
        cloudflare, discord, docker, gcp, k8s,
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
        result = _aio.run(fns[name]())
    elif name == "redis":
        result = _aio.run(registry.state_store().ping())
    elif name == "s3":
        result = _aio.run(registry.checkpoint_store().ping())
    elif name == "docker":
        result = docker.ping()
    elif name == "k8s":
        result = k8s.ping()
    elif name == "gcp":
        result = gcp.ping()
    else:
        print(f"unknown adapter: {name!r}", file=sys.stderr)
        return 2
    for k, v in result.items():
        print(f"  {k:<14} {v}")
    return 0 if result.get("ok") else 1


def _toolkit_chat(prompt: str, model=None, stream: bool = False) -> int:
    import asyncio as _aio
    from agcl.toolkit import registry

    client = registry.get_chat_client()
    if client is None:
        print("no gateway configured. set AGCL_LLM_BASE_URL, VLLM_HOST, "
              "or OLLAMA_HOST.", file=sys.stderr)
        return 1
    use_model = model or getattr(client, "default_model", None) or "smart"
    msgs = [{"role": "user", "content": prompt}]

    async def _run():
        try:
            if stream:
                async for chunk in client.stream(msgs, model=use_model):
                    print(chunk, end="", flush=True)
                print()
            else:
                resp = await client.chat(msgs, model=use_model)
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(content)
        finally:
            await client.aclose()

    _aio.run(_run())
    return 0


if __name__ == "__main__":
    main()
