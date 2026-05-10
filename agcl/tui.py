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
from . import ui
from . import features as F


# Built-in slash commands. Pulls from the central catalog so new ones
# show up without TUI edits.
BASE_COMMANDS = F.slash_command_names()


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
    """Pulls every slash command from the central catalog so new
    additions show up automatically — no TUI edit required."""
    ui.hr()
    print(ui.styled(" AGCL shell · quick reference", bold=True))
    ui.hr()

    # Group commands so the screen reads top-down.
    groups = [
        ("navigation",    ["back", "quit", "help"]),
        ("recursive MAS", ["topics", "rebuild", "halt", "pause", "resume",
                            "status", "latent", "cloud", "continue", "strict"]),
        ("mini trainer",  ["mini", "mini-start", "mini-stop", "mini-pause",
                            "mini-resume", "mini-status", "mini-test",
                            "mini-config", "mini-presets"]),
        ("operations",    ["server", "config", "export", "import", "hint"]),
        ("toolkit",       ["discover", "ping", "emit"]),
    ]
    catalog = F.SLASH_COMMANDS
    for label, cmds in groups:
        print(ui.styled(f"\n {label}", dim=True, italic=True))
        for c in cmds:
            if c not in catalog:
                continue
            print(f"   {ui.cmd('/' + c).ljust(28)}{ui.hint(catalog[c])}")
    print()
    print(ui.hint(" plugin commands appear in any mode as first-class slash"))
    print(ui.hint(" commands; see /node/plugins for the full list."))
    ui.hr()
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
    ui.header("chat mode")
    print(ui.hint("  /back to leave, /quit to exit shell"))
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
            print(ui.hint(f"  [local {int(ms*1000)}ms] ") + prefix, end="", flush=True)
        full = prefix or ""
        async def _run() -> str:
            out = full
            async for chunk in stream_continuation(msgs, prefix, None, "natural",
                                                     session_id=sid, kind="chat"):
                print(chunk, end="", flush=True)
                out += chunk
            return out
        try:
            full = asyncio.run(_run())
        except Exception as e:
            print()
            ui.step_fail(f"{type(e).__name__}: {e}")
            continue
        print()
        sess["messages"] = msgs + [{"role": "assistant", "content": full}]
        st.put_session(sid, sess)


def _run_recursive_mode(session: PromptSession, linter: Linter) -> None:
    """Recursive multi-agent run loop with halt/pause control."""
    print()
    ui.header("recursive mode")
    print(ui.hint("  /halt and /pause work mid-training; /strict toggles "
                   "anti-hallucination mode; /back leaves"))
    from agcl.recursive import build_from_config, RecursiveSession
    from agcl.recursive.session import _strict_mode

    with ui.spinner("building MAS"):
        mas = build_from_config()
    ui.kv("agents", str(len(mas.agents)))
    ui.kv("rounds", str(mas.n_rounds))
    ui.kv("dims",   str(mas.dims))
    sess = RecursiveSession(mas, verbose=False, strict=_strict_mode())
    if sess.strict:
        ui.kv("strict", ui.ok("on") + ui.hint("  (cloud finishes every turn)"))
    else:
        ui.kv("strict", ui.hint("off  (toggle with /strict if outputs hallucinate)"))

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
                print(ui.hint("  no latent recorded yet"))
            else:
                vals = snap["values"]
                print(f"  shape={snap['shape']} stage={snap['stage']} "
                      f"step={snap['step']} first8={vals[:8]}")
            continue
        if line == "/strict":
            sess.strict = not sess.strict
            if sess.strict:
                sess.cloud_continue = True
                sess.prefix_tokens = min(sess.prefix_tokens, 8)
                print(ui.ok("  strict mode ON — cloud will finish every turn"))
            else:
                print(ui.hint("  strict mode off"))
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
            with ui.spinner("running turn"):
                out = asyncio.run(sess.turn(
                    msg, force_cloud=force_cloud, force_continue=force_continue,
                ))
        except Exception as e:
            ui.step_fail(f"{type(e).__name__}: {e}")
            continue
        print(ui.accent("agent >"), out)
        print()


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


def _run_toolkit_mode(session: PromptSession, linter: Linter) -> None:
    """Toolkit REPL: discover / ping / chat / emit-* without leaving the shell."""
    print()
    ui.header("toolkit mode")
    print(ui.hint("  /discover, /ping <adapter>, /chat <msg>, "
                   "/emit docker|k8s|gcp, /back"))
    from agcl.toolkit import registry
    while True:
        try:
            line = session.prompt("toolkit > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line:
            continue
        if line == "/back":  return
        if line == "/quit":  sys.exit(0)
        if line == "/help":  _print_help(); continue

        if line == "/discover":
            for name, info in registry.discover().items():
                cfg = ui.ok("configured") if info["configured"] else ui.hint("off")
                imp = ui.ok("ok") if info["importable"] else ui.err("missing")
                print(f"  {ui.cmd(name.ljust(12))} {cfg.ljust(28)} import:{imp}")
            continue

        if line.startswith("/ping "):
            adapter = line[len("/ping "):].strip()
            try:
                from main import _ping_adapter   # reuse the CLI helper
                _ping_adapter(adapter)
            except Exception as e:
                ui.step_fail(f"{type(e).__name__}: {e}")
            continue

        if line.startswith("/chat "):
            prompt = line[len("/chat "):].strip()
            client = registry.get_chat_client()
            if client is None:
                ui.step_fail("no gateway configured "
                              "(set AGCL_LLM_BASE_URL / VLLM_HOST / OLLAMA_HOST)")
                continue
            model = getattr(client, "default_model", None) or "smart"
            async def _run() -> None:
                try:
                    with ui.spinner(f"{client.endpoint.name} : {model}"):
                        async for chunk in client.stream(
                            [{"role": "user", "content": prompt}], model=model,
                        ):
                            print(chunk, end="", flush=True)
                    print()
                finally:
                    await client.aclose()
            try: asyncio.run(_run())
            except Exception as e: ui.step_fail(f"{type(e).__name__}: {e}")
            continue

        if line.startswith("/emit "):
            kind = line[len("/emit "):].strip().lower()
            try:
                if kind in ("docker", "compose"):
                    from agcl.toolkit import docker as D
                    r = D.write_to("deploy")
                    for f in r["files"]: print(f"  {ui.ok('wrote')} {f}")
                elif kind in ("k8s", "kubernetes", "helm"):
                    from agcl.toolkit import k8s as K
                    chart = K.write_chart("deploy/agcl-chart")
                    mans  = K.write_manifests("deploy/agcl-chart/manifests")
                    for f in chart["files"] + mans["files"]:
                        print(f"  {ui.ok('wrote')} {f}")
                elif kind == "gcp":
                    from agcl.toolkit import gcp as G
                    r = G.write_to("deploy/gcp")
                    for f in r["files"]: print(f"  {ui.ok('wrote')} {f}")
                    print(f"  {ui.cmd('deploy:')} {r['deploy_command']}")
                else:
                    ui.step_fail(f"unknown target: {kind!r} (use docker / k8s / gcp)")
            except Exception as e:
                ui.step_fail(f"{type(e).__name__}: {e}")
            continue

        ui.step_fail(f"unknown command: {line!r}")
        print(ui.hint("  try /discover, /ping <adapter>, /chat <msg>, /emit docker"))


def _run_run_mode(session: PromptSession) -> None:
    """Headless task runner — same as `python main.py run` but in-shell."""
    print()
    ui.header("run mode")
    print(ui.hint("  type a task to execute it via the recursive MAS, "
                   "with jsonl events streaming in real time. /back, /quit."))
    while True:
        try:
            task = session.prompt("task > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not task: continue
        if task == "/back": return
        if task == "/quit": sys.exit(0)
        if task == "/help": _print_help(); continue
        from agcl.runner import run as runner_run
        try:
            runner_run(task, session_id="tui-run", output="text")
        except Exception as e:
            ui.step_fail(f"{type(e).__name__}: {e}")


def _run_orchestrate_mode(session: PromptSession) -> None:
    """Drive a remote AGCL node from this shell."""
    print()
    ui.header("orchestrate mode")
    print(ui.hint("  fan-out to a remote AGCL node. /target <url>, /key <bearer>, "
                   "then enter task lines. /back to leave."))
    target_url: Optional[str] = None
    auth_key: Optional[str] = os.environ.get("AGCL_TARGET_KEY")
    while True:
        try:
            line = session.prompt("orch > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line: continue
        if line == "/back": return
        if line == "/quit": sys.exit(0)
        if line == "/help": _print_help(); continue
        if line.startswith("/target "):
            target_url = line[len("/target "):].strip()
            ui.kv("target", target_url)
            continue
        if line.startswith("/key "):
            auth_key = line[len("/key "):].strip()
            ui.kv("auth", "set" if auth_key else "(none)")
            continue
        if not target_url:
            ui.step_fail("set /target <url> first")
            continue
        from agcl.orchestrator import run as orchestrate
        try:
            orchestrate(target=target_url, auth_key=auth_key, task=line, output="text")
        except Exception as e:
            ui.step_fail(f"{type(e).__name__}: {e}")


def _run_bundle_mode(session: PromptSession) -> None:
    """Config import / export / hint REPL."""
    print()
    ui.header("config bundle mode")
    print(ui.hint("  /export [path], /import <path> [--apply], "
                   "/hint <path>, /show, /back"))
    from agcl import config_bundle as CB
    while True:
        try:
            line = session.prompt("config > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line: continue
        if line == "/back": return
        if line == "/quit": sys.exit(0)
        if line == "/help": _print_help(); continue
        if line == "/show":
            CB.cli("show"); continue
        if line.startswith("/export"):
            parts = line.split(maxsplit=1)
            CB.cli("export", parts[1] if len(parts) > 1 else None); continue
        if line.startswith("/hint"):
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                ui.step_fail("usage: /hint <bundle.json>"); continue
            CB.cli("hint", parts[1]); continue
        if line.startswith("/import"):
            parts = line.split()
            if len(parts) < 2:
                ui.step_fail("usage: /import <bundle.json> [--apply]"); continue
            apply = "--apply" in parts
            target = next((p for p in parts[1:] if not p.startswith("--")), None)
            CB.cli("import", target, apply=apply); continue
        ui.step_fail(f"unknown command: {line!r}")


def _run_dashboard_mode() -> None:
    """Open the dashboard URL. If no node is running, offer to start one."""
    import urllib.request
    import urllib.error
    import webbrowser
    print()
    ui.header("dashboard")
    url = "http://localhost:9876/node/dashboard"
    ui.kv("url", ui.accent(url))
    # Probe the node:
    try:
        with urllib.request.urlopen("http://localhost:9876/node/health",
                                      timeout=1) as _:
            running = True
    except (urllib.error.URLError, OSError):
        running = False
    if not running:
        print(ui.hint("  no node detected on :9876 — start one with the "
                       "'server' menu item or `python main.py node`."))
        return
    ui.step_run(f"opening {url}")
    try:
        webbrowser.open(url)
        ui.step_ok("launched system browser")
    except Exception as e:
        ui.step_fail(f"could not open browser: {e}")
        print(ui.hint(f"  copy this URL into your browser: {url}"))


def _run_usage_mode() -> None:
    """Text-mode summary of the same data the dashboard renders."""
    print()
    ui.header("usage")
    from agcl import usage as U
    snap = U.status_snapshot()
    print(ui.styled(" providers", bold=True))
    for name, p in snap["providers"].items():
        cfg = ui.ok("configured") if p["configured"] else ui.hint("not configured")
        lifetime = p.get("lifetime", {})
        line = (f"  {ui.cmd(name.ljust(10))} {cfg.ljust(28)} "
                f"{ui.hint('tokens')} {lifetime.get('tokens', 0)}  "
                f"{ui.hint('cost')} ${lifetime.get('cost_usd', 0):.4f}")
        print(line)
        q = p.get("quota")
        if q:
            if q.get("max_tokens") is not None:
                print(ui.hint(f"      quota {q['max_tokens']} tokens "
                               f"({q.get('used_tokens',0)} used, "
                               f"{q.get('remaining_tokens')} remaining)"))
            elif q.get("max_credit_usd") is not None:
                print(ui.hint(f"      quota ${q['max_credit_usd']} "
                               f"(${q.get('used_cost_usd',0):.4f} used, "
                               f"${q.get('remaining_credit_usd',0):.4f} remaining)"))
    print()
    print(ui.styled(" sessions (recent)", bold=True))
    for s in U.all_sessions()[:10]:
        print(f"  {ui.cmd(s['session_id'].ljust(20))} "
              f"chat={s['chat_tokens']:>6}  "
              f"knowledge={s['knowledge_tokens']:>6}  "
              f"cost=${s['cost_usd']:.4f}")


def _run_api_mode(session: PromptSession) -> None:
    """API management: quotas + custom OpenAI-compatible providers.
    Mirrors the dashboard's third tab so terminal users have parity."""
    print()
    ui.header("API management")
    print(ui.hint("  /list, /quota <provider> tokens=N usd=N period=lifetime|daily|monthly,"
                   " /clear-quota <provider>,\n  /custom add <name> <url> <env> <model>,"
                   " /custom rm <name>, /back"))
    from agcl import usage as U

    def _show() -> None:
        snap = U.status_snapshot()
        print(ui.styled(" providers", bold=True))
        for name, p in snap["providers"].items():
            cfg = ui.ok("configured") if p["configured"] else ui.hint("not configured")
            life = p.get("lifetime", {})
            line = (f"  {ui.cmd(name.ljust(10))} {cfg.ljust(28)} "
                    f"{ui.hint('tokens')} {life.get('tokens',0)}  "
                    f"{ui.hint('cost')} ${life.get('cost_usd',0):.4f}")
            print(line)
            q = p.get("quota")
            if q:
                if q.get("max_tokens") is not None:
                    print(ui.hint(f"      quota: {q.get('used_tokens',0)} / "
                                   f"{q['max_tokens']} tokens [{q['period']}]"))
                elif q.get("max_credit_usd") is not None:
                    print(ui.hint(f"      quota: ${q.get('used_cost_usd',0):.4f} / "
                                   f"${q['max_credit_usd']} [{q['period']}]"))
        customs = U.list_custom_providers()
        if customs:
            print(ui.styled(" custom providers", bold=True))
            for c in customs:
                print(f"  {ui.cmd(c['name'].ljust(12))} {c['base_url']}  "
                      f"{ui.hint('model='+c.get('model',''))}")

    _show()
    while True:
        try:
            line = session.prompt("api > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line: continue
        if line == "/back": return
        if line == "/quit": sys.exit(0)
        if line == "/help": _print_help(); continue
        if line == "/list": _show(); continue

        if line.startswith("/quota "):
            parts = line[len("/quota "):].split()
            if not parts:
                ui.step_fail("usage: /quota <provider> tokens=N | usd=N [period=lifetime|daily|monthly]")
                continue
            provider = parts[0]
            kwargs: dict = {"period": "lifetime"}
            for tok in parts[1:]:
                if "=" not in tok:
                    continue
                k, v = tok.split("=", 1)
                k = k.strip().lower(); v = v.strip()
                if k in ("tokens", "max_tokens"):
                    kwargs["max_tokens"] = int(v)
                elif k in ("usd", "credit", "max_credit_usd"):
                    kwargs["max_credit_usd"] = float(v)
                elif k == "period":
                    kwargs["period"] = v
                elif k == "notes":
                    kwargs["notes"] = v
            try:
                q = U.set_quota(provider, **kwargs)
                ui.step_ok(f"quota set on {provider}: {q.to_dict()}")
            except Exception as e:
                ui.step_fail(f"{type(e).__name__}: {e}")
            continue

        if line.startswith("/clear-quota "):
            provider = line[len("/clear-quota "):].strip()
            ok = U.clear_quota(provider)
            (ui.step_ok if ok else ui.step_fail)(
                f"{'cleared' if ok else 'no quota for'} {provider}"
            )
            continue

        if line.startswith("/custom "):
            parts = line[len("/custom "):].split()
            if not parts:
                ui.step_fail("usage: /custom add|rm|list ..."); continue
            sub = parts[0]
            if sub == "list":
                for c in U.list_custom_providers():
                    print(f"  {ui.cmd(c['name'])} {c['base_url']} "
                          f"{ui.hint('env='+c.get('api_key_env',''))} "
                          f"{ui.hint('model='+c.get('model',''))}")
                continue
            if sub == "rm":
                if len(parts) < 2:
                    ui.step_fail("usage: /custom rm <name>"); continue
                ok = U.unregister_custom_provider(parts[1])
                (ui.step_ok if ok else ui.step_fail)(
                    f"{'removed' if ok else 'no such provider'}: {parts[1]}"
                )
                continue
            if sub == "add":
                if len(parts) < 5:
                    ui.step_fail("usage: /custom add <name> <base_url> <env_var> <model>")
                    continue
                try:
                    spec = U.register_custom_provider(
                        parts[1], base_url=parts[2],
                        api_key_env=parts[3], model=parts[4],
                    )
                    ui.step_ok(f"registered {spec['name']}")
                except Exception as e:
                    ui.step_fail(f"{type(e).__name__}: {e}")
                continue
            ui.step_fail(f"unknown custom subcommand: {sub!r}")
            continue

        ui.step_fail(f"unknown command: {line!r}")


def _run_sessions_mode(session: PromptSession) -> None:
    """Manage running RecursiveMAS sessions: list / view / halt / pause /
    resume / latent / delete. Pulls from the same in-memory session map
    the node uses, so this works whether or not the HTTP server is up."""
    print()
    ui.header("MAS sessions")
    print(ui.hint("  /list, /view <sid>, /halt <sid>, /pause <sid>, /resume <sid>,"
                   " /latent <sid>, /delete <sid>, /back"))
    from agcl.node import _mas_sessions   # in-process map

    def _list() -> None:
        if not _mas_sessions:
            print(ui.hint("  (no live sessions — fire one through chat or recursive mode first)"))
            return
        for sid, s in _mas_sessions.items():
            tid = s.current_topic_id or "(no topic)"
            line = (f"  {ui.cmd(sid.ljust(20))} topic={ui.accent(tid)}  "
                    f"trained={ui.ok('yes') if s.trained_once else ui.hint('no')}  "
                    f"turns={len(s.history)//2}")
            print(line)

    _list()
    while True:
        try:
            line = session.prompt("sessions > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line: continue
        if line == "/back":  return
        if line == "/quit":  sys.exit(0)
        if line == "/help":  _print_help(); continue
        if line == "/list":  _list(); continue

        head, _, sid = line.partition(" ")
        sid = sid.strip()
        if not sid:
            ui.step_fail(f"usage: {head} <session_id>"); continue
        s = _mas_sessions.get(sid)
        if s is None:
            ui.step_fail(f"no session {sid!r} (try /list)"); continue

        if head == "/view":
            print(f"  topic_id  {ui.accent(s.current_topic_id or '(none)')}")
            print(f"  trained   {ui.ok('yes') if s.trained_once else ui.hint('no')}")
            print(f"  history   {len(s.history)} messages")
            for msg in s.history[-6:]:
                role = msg.get("role", "")
                content = (msg.get("content","") or "").splitlines()[0][:80]
                col = ui.accent if role == "user" else ui.success
                print(f"    {col(role.ljust(10))} {content}")
        elif head == "/halt":
            s.control.halt(); ui.step_ok(f"halt signaled on {sid}")
        elif head == "/pause":
            s.control.pause(); ui.step_ok(f"paused {sid}")
        elif head == "/resume":
            s.control.resume(); ui.step_ok(f"resumed {sid}")
        elif head == "/latent":
            snap = s.control.latent_snapshot()
            if snap is None:
                print(ui.hint("  no latent recorded yet"))
            else:
                print(f"  shape={snap['shape']} stage={snap['stage']} "
                      f"step={snap['step']} first8={snap['values'][:8]}")
        elif head == "/delete":
            del _mas_sessions[sid]; ui.step_ok(f"dropped {sid} (on-disk topic untouched)")
        else:
            ui.step_fail(f"unknown command: {head!r}")


def _run_diagnostics_mode() -> None:
    """One-shot health check: pings every toolkit adapter, runs the
    21 recursive validation checks, hints on the active config bundle,
    detects external tools."""
    print()
    ui.header("diagnostics")

    # 1. External tools detected on PATH
    from agcl import tools as T
    print(ui.styled(" external tools", bold=True))
    inv = T.detect_all()
    for name, info in sorted(inv.items()):
        if info["ok"]:
            v = (info["version"] or "")[:60]
            print(f"  {ui.ok('✓').ljust(2)} {ui.cmd(name.ljust(10))} {ui.hint(v)}")
        else:
            print(f"  {ui.hint('-')} {ui.hint(name.ljust(10))} {ui.hint('not on PATH')}")

    # 2. Toolkit adapter discovery
    print()
    print(ui.styled(" toolkit adapters", bold=True))
    from agcl.toolkit import registry
    for name, info in registry.discover().items():
        cfg = ui.ok("configured") if info["configured"] else ui.hint("off")
        imp = ui.ok("ok") if info["importable"] else ui.err("missing")
        print(f"  {ui.cmd(name.ljust(12))} {cfg.ljust(28)} import:{imp}")

    # 3. Config bundle validation against current env
    print()
    print(ui.styled(" config bundle (against current env)", bold=True))
    try:
        from agcl import config_bundle as CB
        bundle = CB.export("/tmp/.agcl-diag-bundle.json")
        report = CB.validate(bundle)
        ui.kv("status", ui.ok("ready") if report["ok"] else ui.err("incomplete"))
        if report["missing_files"]: ui.kv("missing files", str(report["missing_files"]))
        if report["missing_hf"]:    ui.kv("missing HF",    str(report["missing_hf"]))
        if report["missing_env"]:   ui.kv("missing env",   str(report["missing_env"]))
        if report["missing_extras"]: ui.kv("missing extras", str(report["missing_extras"]))
        for h in report["hints"][:6]:
            print(ui.hint(f"  > {h}"))
        import os; os.unlink("/tmp/.agcl-diag-bundle.json")
    except Exception as e:
        ui.step_fail(f"bundle validate: {type(e).__name__}: {e}")

    # 4. RecursiveMAS validate (21 unit checks)
    print()
    print(ui.styled(" recursive validation", bold=True))
    try:
        with ui.spinner("running 21 checks"):
            from agcl.recursive import validate as rv
            ok = rv.run_all()
        if ok: ui.step_ok("all 21 checks pass")
        else:  ui.step_fail("some checks failed (see output above)")
    except Exception as e:
        ui.step_fail(f"validate: {type(e).__name__}: {e}")

    # 5. Pressure / patterns snapshot (in-process)
    print()
    print(ui.styled(" runtime pressure / patterns", bold=True))
    try:
        from agcl import pressure as P, patterns as PT, state as S
        ui.kv("pressure",     str(P.pressure()))
        ui.kv("active hours", str(PT.active_hours()))
        ui.kv("idle sec",     f"{S.idle_seconds():.0f}")
        ui.kv("sessions",     str(len(S.session_list())))
    except Exception as e:
        ui.step_fail(f"snapshot: {type(e).__name__}: {e}")


def _run_secrets_mode(session: PromptSession) -> None:
    """In-place .env editor with masking. Set / unset / list keys safely.
    Values are NEVER echoed back — `/list` shows length-only previews
    for anything name-resembling a secret."""
    print()
    ui.header("secrets / .env editor")
    print(ui.hint("  /list, /set <KEY> <value>, /unset <KEY>, /test <KEY>, /back"))
    from pathlib import Path

    SECRET_TOKENS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH")
    p = Path(".env")

    def _read() -> dict:
        if not p.exists(): return {}
        out: dict = {}
        for ln in p.read_text().splitlines():
            s = ln.strip()
            if not s or s.startswith("#") or "=" not in s: continue
            k, _, v = s.partition("=")
            out[k.strip()] = v
        return out

    def _write(kv: dict) -> None:
        p.write_text("\n".join(f"{k}={v}" for k, v in kv.items()) + "\n")

    def _is_secret(k: str) -> bool:
        return any(t in k for t in SECRET_TOKENS)

    while True:
        try:
            line = session.prompt("env > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line: continue
        if line == "/back": return
        if line == "/quit": sys.exit(0)
        if line == "/help": _print_help(); continue

        if line == "/list":
            kv = _read()
            if not kv:
                print(ui.hint("  (.env is empty or missing)")); continue
            for k, v in sorted(kv.items()):
                disp = (f"***({len(v)} chars)" if _is_secret(k) and v else v) or "(empty)"
                print(f"  {ui.cmd(k.ljust(28))} {ui.hint(disp)}")
            continue

        if line.startswith("/set "):
            rest = line[len("/set "):].strip()
            if " " not in rest:
                ui.step_fail("usage: /set <KEY> <value>"); continue
            k, _, v = rest.partition(" ")
            k = k.strip(); v = v.strip()
            if not k.replace("_","").isalnum():
                ui.step_fail("KEY must be alnum + underscore"); continue
            kv = _read(); kv[k] = v; _write(kv)
            import os; os.environ[k] = v
            ui.step_ok(f"set {k} ({'masked' if _is_secret(k) else v})")
            continue

        if line.startswith("/unset "):
            k = line[len("/unset "):].strip()
            kv = _read()
            if k not in kv:
                ui.step_fail(f"{k} not in .env"); continue
            del kv[k]; _write(kv)
            import os; os.environ.pop(k, None)
            ui.step_ok(f"unset {k}")
            continue

        if line.startswith("/test "):
            k = line[len("/test "):].strip()
            import os
            v = os.environ.get(k)
            ui.kv(k, ("(set, masked)" if _is_secret(k) and v else (v or "(unset)")))
            continue

        ui.step_fail(f"unknown command: {line!r}")


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
        # Build the menu from the central feature registry, grouped.
        # Plugin commands appear at the bottom under their own header.
        order = [
            # agent
            "chat", "recursive", "run", "orchestrate", "minimodel",
            "sessions", "topics",
            # infra
            "toolkit", "server", "dashboard", "usage", "api",
            # ops
            "diagnostics", "bundle", "secrets", "config", "autoconfig",
            # ext
            "plugins", "mcp", "slack", "discord", "agentmod", "openapi",
            # core
            "help", "quit",
        ]
        items: List[MenuItem] = []
        for name in order:
            f = F.find(name)
            if f is None:
                continue
            items.append((name, f.label))

        try:
            choice = menu("AGCL · main menu", items)
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice is None or choice == "quit":
            print(ui.hint("bye"))
            return 0

        if choice == "chat":           _run_chat_mode(session, linter)
        elif choice == "recursive":    _run_recursive_mode(session, linter)
        elif choice == "run":          _run_run_mode(session)
        elif choice == "orchestrate":  _run_orchestrate_mode(session)
        elif choice == "minimodel":    _run_minimodel_mode(session)
        elif choice == "topics":       _run_topics_mode()
        elif choice == "toolkit":      _run_toolkit_mode(session, linter)
        elif choice == "server":       _run_server_mode()
        elif choice == "dashboard":    _run_dashboard_mode()
        elif choice == "usage":        _run_usage_mode()
        elif choice == "bundle":       _run_bundle_mode(session)
        elif choice == "config":
            from agcl.configurator import run as run_configurator
            run_configurator()
        elif choice == "autoconfig":
            from agcl.autoconfig import run as run_autoconfig
            run_autoconfig()
        elif choice == "plugins":      _run_plugins_mode()
        elif choice == "api":          _run_api_mode(session)
        elif choice == "sessions":     _run_sessions_mode(session)
        elif choice == "diagnostics":  _run_diagnostics_mode()
        elif choice == "secrets":      _run_secrets_mode(session)
        elif choice == "openapi":
            # OpenAPI dump runs in-process and exits cleanly — no need to
            # send the user out to a separate terminal.
            from agcl.integrations import openapi_export as OA
            try:
                with ui.spinner("dumping OpenAPI spec to agcl.openapi.json"):
                    OA.export(out="agcl.openapi.json")
            except Exception as e:
                ui.step_fail(f"{type(e).__name__}: {e}")
        elif choice == "mcp":
            print(ui.hint("  the MCP server takes over stdio — running it from a "
                           "menu would deadlock. Open a new terminal and run:\n  "
                           f"   {ui.cmd('python main.py mcp')}  "
                           f"{ui.hint('(or')} {ui.cmd('python main.py mcp --fast')}"
                           f"{ui.hint(')')}"))
        elif choice in ("slack", "discord", "agentmod"):
            f = F.find(choice)
            print(ui.hint(f"  these adapters are long-running. Open a new terminal "
                           f"and run:\n     {ui.cmd(f.cli)}"))
        elif choice == "help":
            _print_help()
        else:
            # Could be a plugin command name surfaced in the menu later.
            try:
                plg.run_command(choice, "")
            except KeyError:
                ui.step_fail(f"no handler for {choice!r}")
