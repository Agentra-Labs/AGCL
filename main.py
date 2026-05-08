"""
Entry point. Run with:
  python main.py                         # CLI client (auto-starts server)
  python main.py --session work          # named session
  python main.py --provider openai       # force provider
  python main.py --recovery humor        # recovery mode
  python main.py serve --port 8000       # run the server in the foreground
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
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

import openslock.state as state
import openslock.pressure as prs
import openslock.patterns as patterns
import openslock.context as context
from openslock.local_llm import generate_prefix, unload as unload_local_model
from openslock.cloud import stream_continuation
from openslock.config import IDLE_FLUSH_SEC

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


def _run_recursive(args):
    """Recursive-MAS feature: validate, inspect config, or run a prompt."""
    from openslock.recursive import validate as rv

    if args.action == "validate":
        ok = rv.run_all()
        sys.exit(0 if ok else 1)

    if args.action == "info":
        from openslock import config as cfg
        from openslock.recursive import persistence as rp
        print("openslock — RecursiveMAS modules:")
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
        from openslock.recursive import build_from_config, RecursiveSession

        print("[openslock] building MAS from config...")
        mas = build_from_config()
        print(f"[openslock] {len(mas.agents)} agents, {mas.n_rounds} rounds, dims={mas.dims}")

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
            "\n[openslock] interactive mode\n"
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
                print(f"[openslock] error: {type(e).__name__}: {e}")
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
    from openslock.recursive import persistence as P
    rows = P.list_topics(state_dir)
    if not rows:
        print("  (no saved topics)")
        return
    for r in rows:
        print(f"  {r['topic_id']}  dims={r.get('signature',{}).get('dims')}  "
              f"seed={r.get('seed_question','')[:60]!r}")


def main():
    ap = argparse.ArgumentParser(prog="openslock")
    sub = ap.add_subparsers(dest="cmd")

    # `serve` runs the FastAPI app via uvicorn
    sp = sub.add_parser("serve", help="run the FastAPI server")
    sp.add_argument("--host",      default="127.0.0.1")
    sp.add_argument("--port",      default=DEFAULT_PORT, type=int)
    sp.add_argument("--reload",    action="store_true")
    sp.add_argument("--log-level", default="info")

    # `chat` (default) opens the terminal client
    cp = sub.add_parser("chat", help="open the terminal client (default)")
    cp.add_argument("--session",  default="default")
    cp.add_argument("--provider", default=None, choices=["openai", "claude"])
    cp.add_argument("--recovery", default="natural", choices=["natural", "humor", "explicit"])
    cp.add_argument("--port",     default=DEFAULT_PORT, type=int)

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
        from openslock.configurator import run as run_configurator
        sys.exit(run_configurator())

    if args.autoconfig:
        from openslock.autoconfig import run as run_autoconfig
        sys.exit(run_autoconfig())

    if args.cmd == "serve":
        _run_serve(args)
    elif args.cmd == "recursive":
        _run_recursive(args)
    elif args.cmd == "autoconfig":
        from openslock.autoconfig import run as run_autoconfig
        sys.exit(run_autoconfig(
            force=args.force, install_deps=args.install_deps, download=args.download,
        ))
    else:
        _run_cli(args)


if __name__ == "__main__":
    main()
