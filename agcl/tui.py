"""
AGCL persistent TUI shell.

A long-running interactive shell built on prompt_toolkit. Replaces the
old chat-only REPL with a menu-driven experience:

    [main menu]
        > chat (passthrough cloud agent)
          recursive (multi-agent reasoning)
          topics (saved trained-state index)
          server (start node HTTP+SSE server in this process)
          config (open the interactive RecursiveMAS configurator)
          plugins (list discovered plugins)
          quit

The user navigates with arrow keys and Enter. From any sub-mode, `/back`
returns to the main menu, `/quit` exits the shell. Plugins can register
top-level commands that show up in the menu under a "plugins" submenu.

The shell never auto-starts the FastAPI server — the server stays a
deliberate, named feature. Pick "server" from the menu to launch it in
this process.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Callable, Dict, List, Optional, Tuple

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style

from .banner import banner
from .lint import Linter


# Built-in slash commands the linter knows about. Plugins extend this.
BASE_COMMANDS = [
    "back", "quit", "help",
    "topics", "rebuild", "halt", "pause", "resume", "status", "latent",
    "cloud", "continue", "server", "config",
    # mini-model background trainer
    "mini", "mini-start", "mini-stop", "mini-pause", "mini-resume",
    "mini-status", "mini-test", "mini-config", "mini-presets",
]


# --- theme: pure black/white, inverted by terminal background -------
# Dark terminal -> white text on black ; light terminal -> black on
# white. No accent colors anywhere; the CLI looks the same in either
# mode, just inverted. Override with `AGCL_THEME=light|dark`.

def _detect_theme() -> str:
    forced = os.environ.get("AGCL_THEME", "").strip().lower()
    if forced in ("light", "dark"):
        return forced
    # COLORFGBG ("fg;bg") is set by some terminals. bg index in
    # {0,1,2,3,4,5,6,8} -> dark base; {7,9,10,11,12,13,14,15} -> light.
    cfb = os.environ.get("COLORFGBG", "")
    if ";" in cfb:
        try:
            bg = int(cfb.rsplit(";", 1)[1])
            return "light" if bg in (7, 9, 10, 11, 12, 13, 14, 15) else "dark"
        except ValueError:
            pass
    return "dark"   # safest default for terminals


THEME = _detect_theme()
_FG = "#ffffff" if THEME == "dark" else "#000000"
_BG = "#000000" if THEME == "dark" else "#ffffff"

_STYLE = Style.from_dict({
    # selectable menu rows: invert fg/bg vs the terminal so a row visibly
    # highlights without using any accent color.
    "dialog":             f"bg:{_BG} {_FG}",
    "dialog frame.label": f"bg:{_BG} {_FG} bold",
    "dialog.body":        f"bg:{_BG} {_FG}",
    "radio":              f"bg:{_BG} {_FG}",
    "radio-selected":     f"bg:{_FG} {_BG} bold",
    "radio-checked":      f"bg:{_BG} {_FG} bold",
    "button":             f"bg:{_BG} {_FG}",
    "button.focused":     f"bg:{_FG} {_BG} bold",
})


def _print_hint(text: str) -> None:
    """Render a single dim inline hint, Claude-Code-style. Only fires on
    text the linter flagged - not on tool output, not on every input.
    Uses ANSI dim (\\033[2m) which both light and dark terminals render
    as a faded version of the foreground color, so it stays
    monochromatic."""
    if not text:
        return
    sys.stdout.write(f"\033[2m  {text}\033[0m\n")
    sys.stdout.flush()


def _print_banner() -> None:
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 60
    width = min(48, max(30, cols - 2))
    print(banner(width=width))


def _print_help() -> None:
    print()
    print("=" * 50)
    print(" AGCL shell - quick reference")
    print("=" * 50)
    print(" /help       show this reference")
    print(" /back       return to the main menu")
    print(" /quit       exit the shell")
    print(" /topics     list saved trained topics")
    print(" /halt       halt training in current session")
    print(" /pause      pause training (resumable)")
    print(" /resume     resume after a /pause")
    print(" /status     show training control state")
    print(" /latent     dump latest latent snapshot (truncated)")
    print(" /cloud msg  one turn forced through the cloud only")
    print(" /continue m one turn local-prefix + cloud-finish")
    print()
    print(" Plugin commands appear under /plugins in the menu and as")
    print(" first-class slash commands in any mode.")
    print("=" * 50)
    print()


# ------------------------------------------------------------------
# Menu primitives
# ------------------------------------------------------------------

MenuItem = Tuple[str, str]   # (key, label)


def menu(title: str, items: List[MenuItem],
         default: Optional[str] = None) -> Optional[str]:
    """
    Render an arrow-key menu, return the chosen key or None on cancel.
    Uses prompt_toolkit's radiolist_dialog under the hood.
    """
    values = [(k, FormattedText([("class:menu", f"  {label}  ")]))
              for k, label in items]
    return radiolist_dialog(
        title=title,
        values=values,
        default=default or items[0][0],
        style=_STYLE,
    ).run()


# ------------------------------------------------------------------
# Sub-modes
# ------------------------------------------------------------------

def _run_chat_mode(session: PromptSession, linter: Linter) -> None:
    """Plain prefix+cloud chat, in-process (no HTTP)."""
    print()
    print("== chat mode ==  /back to leave, /quit to exit shell")
    from agcl import state as st
    from agcl.local_llm import generate_prefix
    from agcl.cloud import stream_continuation

    sid = "tui-chat"
    while True:
        try:
            line = session.prompt("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        # Claude-Code-style: only flag genuine slash-command typos with
        # a single dim hint line. Plain prose passes through silently.
        if line.startswith("/"):
            _print_hint(linter(line).hint())
        if line == "/quit":
            sys.exit(0)
        if line == "/back":
            return
        if line == "/help":
            _print_help(); continue
        if _handle_mini_slash(line, session):
            continue

        sess = st.get_session(sid)
        msgs = sess["messages"] + [{"role": "user", "content": line}]
        prefix, ms = generate_prefix(msgs)
        if prefix:
            print(f"  [local {int(ms*1000)}ms] {prefix}", end="", flush=True)
        full = prefix or ""
        async def _run() -> str:
            out = full
            async for chunk in stream_continuation(msgs, prefix, None, "natural"):
                print(chunk, end="", flush=True)
                out += chunk
            return out
        try:
            full = asyncio.run(_run())
        except Exception as e:
            print(f"\n  [err] {type(e).__name__}: {e}")
            continue
        print()
        sess["messages"] = msgs + [{"role": "assistant", "content": full}]
        st.put_session(sid, sess)


def _run_recursive_mode(session: PromptSession, linter: Linter) -> None:
    """Recursive multi-agent run loop with halt/pause control."""
    print()
    print("== recursive mode ==  /halt and /pause work mid-training")
    from agcl.recursive import build_from_config, RecursiveSession

    print("  building MAS...")
    mas = build_from_config()
    print(f"  {len(mas.agents)} agents, {mas.n_rounds} rounds, dims={mas.dims}")
    sess = RecursiveSession(mas, verbose=False)

    while True:
        try:
            line = session.prompt("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.startswith("/"):
            result = linter(line)
            _print_hint(result.hint())
            if not result.ok:
                # Don't proceed with a malformed slash command.
                continue

        if line == "/quit":
            sys.exit(0)
        if line == "/back":
            return
        if line == "/help":
            _print_help(); continue
        if _handle_mini_slash(line, session):
            continue
        if line == "/topics":
            from agcl.recursive import persistence as P
            for r in P.list_topics(sess.state_dir):
                print(f"  {r['topic_id']}  seed={r.get('seed_question','')[:60]!r}")
            continue
        if line == "/halt":
            sess.control.halt()
            print("  halt signalled (effective at next training step)")
            continue
        if line == "/pause":
            sess.control.pause()
            print("  paused (issue /resume to continue)")
            continue
        if line == "/resume":
            sess.control.resume()
            print("  resumed")
            continue
        if line == "/status":
            print(f"  {sess.control.status()}")
            continue
        if line == "/latent":
            snap = sess.control.latent_snapshot()
            if snap is None:
                print("  no latent recorded yet")
            else:
                vals = snap["values"]
                print(f"  shape={snap['shape']} stage={snap['stage']} "
                      f"step={snap['step']} first8={vals[:8]}")
            continue

        force_cloud = False
        force_continue = False
        msg = line
        if line.startswith("/cloud "):
            force_cloud = True
            msg = line[len("/cloud "):].strip()
        elif line.startswith("/continue "):
            force_continue = True
            msg = line[len("/continue "):].strip()
        if not msg:
            continue

        try:
            out = asyncio.run(sess.turn(
                msg, force_cloud=force_cloud, force_continue=force_continue,
            ))
        except Exception as e:
            print(f"  [err] {type(e).__name__}: {e}")
            continue
        print(f"agent > {out}\n")


def _run_server_mode() -> None:
    """Start the node HTTP+SSE server in this process. Server is a
    feature, not the default — must be opted into from the menu."""
    print()
    import uvicorn
    from agcl.node import build_node_app, current_key
    custom = os.environ.get("AGCL_NODE_AUTH") or None
    build_node_app(auth_key=custom, cors_origins=["*"])
    print("=" * 50)
    print(" AGCL node - ready")
    print("=" * 50)
    print(f"  bind: 127.0.0.1:9876")
    print(f"  auth: {current_key()}")
    print()
    print("  server is in foreground; ctrl+c to stop and return")
    print("=" * 50)
    try:
        uvicorn.run("main:app", host="127.0.0.1", port=9876,
                     log_level="warning")
    except KeyboardInterrupt:
        print("  server stopped")


def _run_topics_mode() -> None:
    from agcl import config as cfg
    from agcl.recursive import persistence as P
    rows = P.list_topics(cfg.STATE_DIR)
    if not rows:
        print("  (no saved topics)")
        return
    for r in rows:
        sig = r.get("signature", {})
        print(f"  {r['topic_id']}  dims={sig.get('dims')} "
              f"seed={r.get('seed_question','')[:60]!r}")


def _run_minimodel_mode(session: PromptSession) -> None:
    """Interactive control surface for the background mini-trainer."""
    from agcl.mini import runtime as M
    print()
    print("== minimodel ==  /back leaves; /mini-test prompt to sample")
    while True:
        items: List[MenuItem] = [
            ("status",  "status - running/paused/step/loss"),
            ("start",   "start - enable + spin up the background thread"),
            ("stop",    "stop - halt the trainer"),
            ("pause",   "pause - suspend (resumable)"),
            ("resume",  "resume - after pause"),
            ("test",    "test - generate from the mini model"),
            ("config",  "config - inspect / edit MiniConfig"),
            ("presets", "presets - list strategies + attention + archs"),
            ("ckpt",    "checkpoint - force save"),
            ("back",    "back - return to main menu"),
        ]
        try:
            pick = menu("AGCL - minimodel", items)
        except (EOFError, KeyboardInterrupt):
            return
        if pick is None or pick == "back":
            return
        t = M.get_trainer()
        if pick == "status":
            for k, v in t.status().items():
                print(f"  {k:<14} {v}")
        elif pick == "start":
            t.config.enabled = True
            t.start()
            print("  trainer started (low priority, sleeping between steps)")
        elif pick == "stop":
            t.stop()
            print("  trainer stopped")
        elif pick == "pause":
            t.pause(); print("  paused")
        elif pick == "resume":
            t.resume(); print("  resumed")
        elif pick == "test":
            try:
                p = session.prompt("  mini-test prompt > ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not p:
                continue
            r = t.test(p)
            print(f"  step={r['step']}  arch={r['arch']}  strategy={r['strategy']}")
            print(f"  decoded > {r['decoded']!r}")
        elif pick == "config":
            for k, v in t.config.to_dict().items():
                print(f"  {k:<14} {v}")
        elif pick == "presets":
            from agcl.mini.strategies import PRESETS
            from agcl.mini.attention import ATTENTION_MASKS
            print("  strategies:")
            for k, comp in PRESETS.items():
                print(f"    {k:<18} {comp}")
            print("  attention presets:", list(ATTENTION_MASKS.keys()))
            print("  archs: transformer | mlp")
        elif pick == "ckpt":
            t.force_checkpoint(); print("  checkpoint saved")
        print()


def _handle_mini_slash(line: str, session: Optional[PromptSession] = None) -> bool:
    """Handle /mini-* slash commands from any sub-mode. Returns True if
    the line was a mini command (handled or rejected)."""
    if not line.startswith("/mini"):
        return False
    from agcl.mini import runtime as M
    t = M.get_trainer()
    head, _, tail = line.partition(" ")
    head = head.lower()
    if head in ("/mini", "/mini-status"):
        for k, v in t.status().items():
            print(f"  {k:<14} {v}")
    elif head == "/mini-start":
        t.config.enabled = True; t.start()
        print("  mini trainer started")
    elif head == "/mini-stop":
        t.stop(); print("  mini trainer stopped")
    elif head == "/mini-pause":
        t.pause(); print("  mini trainer paused")
    elif head == "/mini-resume":
        t.resume(); print("  mini trainer resumed")
    elif head == "/mini-test":
        prompt = tail.strip() or "hello"
        r = t.test(prompt)
        print(f"  step={r['step']} arch={r['arch']} strategy={r['strategy']}")
        print(f"  decoded > {r['decoded']!r}")
    elif head == "/mini-config":
        for k, v in t.config.to_dict().items():
            print(f"  {k:<14} {v}")
    elif head == "/mini-presets":
        from agcl.mini.strategies import PRESETS
        from agcl.mini.attention import ATTENTION_MASKS
        print(f"  strategies: {list(PRESETS.keys())}")
        print(f"  attention:  {list(ATTENTION_MASKS.keys())}")
        print("  archs: transformer | mlp")
    else:
        print(f"  unknown mini command: {head}")
    return True


def _run_plugins_mode() -> None:
    from agcl import plugins as P
    rows = P.loaded()
    if not rows:
        print("  (no plugins loaded; drop a .py file in plugins/ and restart)")
        return
    for r in rows:
        flag = "ok " if r["ok"] else "ERR"
        print(f"  [{flag}] {r['name']:<20} {r.get('error','')}")
    cmds = P.commands()
    if cmds:
        print()
        print("  registered commands:")
        for name, spec in sorted(cmds.items()):
            print(f"    /{name:<14} {spec.get('help','')}")


# ------------------------------------------------------------------
# Main loop
# ------------------------------------------------------------------

def run() -> int:
    _print_banner()

    # Plugin discovery: load now so their commands show in the linter
    # and menu. We pass a throwaway router because plugins may also
    # register HTTP routes — those will register again when /node starts
    # (idempotent because each call uses a fresh APIRouter).
    from fastapi import APIRouter
    from agcl import plugins as plg
    plg.load_plugins(APIRouter())
    plugin_cmds = list(plg.commands().keys())

    linter = Linter(commands=BASE_COMMANDS + plugin_cmds)
    session = PromptSession(
        completer=WordCompleter(
            ["/" + c for c in linter.commands()], ignore_case=True,
        ),
    )

    while True:
        items: List[MenuItem] = [
            ("chat",      "chat - prefix + cloud passthrough"),
            ("recursive", "recursive - multi-agent reasoning"),
            ("minimodel", "minimodel - background trainer (toggleable)"),
            ("topics",    "topics - list saved trained-state"),
            ("config",    "config - interactive configurator"),
            ("server",    "server - start node (optional)"),
            ("plugins",   "plugins - list discovered + commands"),
            ("help",      "help - command reference"),
            ("quit",      "quit"),
        ]
        try:
            choice = menu("AGCL - main menu", items)
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice is None or choice == "quit":
            print("bye")
            return 0
        if choice == "chat":
            _run_chat_mode(session, linter)
        elif choice == "recursive":
            _run_recursive_mode(session, linter)
        elif choice == "minimodel":
            _run_minimodel_mode(session)
        elif choice == "topics":
            _run_topics_mode()
        elif choice == "config":
            from agcl.configurator import run as run_configurator
            run_configurator()
        elif choice == "server":
            _run_server_mode()
        elif choice == "plugins":
            _run_plugins_mode()
        elif choice == "help":
            _print_help()
        else:
            # Could be a plugin command name surfaced in the menu later.
            try:
                plg.run_command(choice, "")
            except KeyError:
                print(f"  no handler for {choice!r}")
