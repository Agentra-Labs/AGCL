"""
agcl.bot_runtime — lifecycle manager for long-running adapter bots.

Bots historically had to be run in a separate terminal:

    python main.py toolkit discord
    python main.py slack --http --port 3000
    python main.py mcp --transport sse --port 8765
    ...

That sucks for the GUI use case. This module wraps each such command
as a managed **subprocess** that the running AGCL node can start,
stop, restart, inspect, and tail logs from.

Why subprocess and not threads:
    - Clean kill. Discord.py runs `bot.run(token)` which installs its
      own asyncio event loop and signal handlers. Killing it cleanly
      from another thread is fragile. SIGTERM on a child process is
      surgical.
    - Crash isolation. If the bot blows up, the node keeps serving.
    - Independent stdout/stderr we can capture and stream.

Each managed bot has:
    - a name (key in BOTS registry)
    - a stable command-line (built from sys.executable + main.py args)
    - a list of env-var prerequisites that must be set, else start fails
    - an in-memory rolling log (last 500 lines of stdout+stderr)

This module is import-safe even when the bots have unmet env vars or
their optional SDKs aren't installed — bots are only spawned on
explicit `start()`.
"""

from __future__ import annotations

import collections
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON       = sys.executable
MAIN         = str(PROJECT_ROOT / "main.py")


# ============================================================
# Registry
# ============================================================

BOTS: Dict[str, Dict[str, Any]] = {
    "discord": {
        "title": "Discord bot",
        "summary": "Listens on the Discord gateway. /ask + @mention route through AGCL.",
        "cmd":   [PYTHON, MAIN, "toolkit", "discord"],
        "requires_env": ["DISCORD_BOT_TOKEN"],
        "requires_import": ["discord"],
        "category": "bot",
        "wizard": "discord",
        # Reasoning-mode env var the bot reads at call time. UI lets
        # the user flip between fast (prefix+cloud) and mas (recursive
        # multi-agent + online training).
        "mode_env": "AGCL_DISCORD_MODE",
        "mode_default": "fast",
    },
    "slack": {
        "title": "Slack bot (HTTP mode, port 3000)",
        "summary": "Bolt adapter. Configure /ask slash command + event subscription URLs.",
        "cmd":   [PYTHON, MAIN, "slack", "--http", "--port", "3000"],
        "requires_env": ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET"],
        "requires_import": ["slack_bolt"],
        "category": "bot",
        "wizard": "slack",
        "mode_env": "AGCL_SLACK_MODE",
        "mode_default": "fast",
    },
    "mcp-sse": {
        "title": "MCP server (SSE, port 8765)",
        "summary": "MCP over Server-Sent Events. Use for tools-only IDE integrations.",
        "cmd":   [PYTHON, MAIN, "mcp", "--transport", "sse", "--host", "127.0.0.1", "--port", "8765"],
        "requires_env": [],
        "requires_import": ["mcp"],
        "category": "infra",
        "wizard": None,
    },
    "agentmod": {
        "title": "OpenAgents AgentMod",
        "summary": "Adapter for the openagents framework.",
        "cmd":   [PYTHON, MAIN, "agentmod"],
        "requires_env": [],
        "requires_import": [],
        "category": "infra",
        "wizard": None,
    },
}


def list_bots_meta() -> List[Dict[str, Any]]:
    return [{"name": k, **v, "cmd": list(v["cmd"])} for k, v in BOTS.items()]


# ============================================================
# Managed bot
# ============================================================

class ManagedBot:
    """A single running bot subprocess. One instance per BOT name,
    held by `_BOTS` below. Thread-safe (mutates only under `_lock`)."""

    LOG_LINES = 500

    def __init__(self, name: str, meta: Dict[str, Any]):
        self.name = name
        self.meta = meta
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: float = 0.0
        self.stopped_at: float = 0.0
        self.exit_code: Optional[int] = None
        self.log: collections.deque = collections.deque(maxlen=self.LOG_LINES)
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ---- state -------------------------------------------------------

    def is_alive(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            alive = self.proc is not None and self.proc.poll() is None
            if self.proc and not alive and self.exit_code is None:
                # Process exited since last check — capture code now.
                self.exit_code = self.proc.returncode
                self.stopped_at = time.time()
            missing_env = [k for k in self.meta.get("requires_env", [])
                           if not os.environ.get(k)]
            missing_imp = [m for m in self.meta.get("requires_import", [])
                           if not _can_import(m)]
            mode_env = self.meta.get("mode_env")
            mode_default = self.meta.get("mode_default", "fast")
            current_mode = os.environ.get(mode_env) if mode_env else None
            return {
                "name":          self.name,
                "title":         self.meta.get("title", self.name),
                "summary":       self.meta.get("summary", ""),
                "category":      self.meta.get("category", ""),
                "wizard":        self.meta.get("wizard"),
                "pid":           self.proc.pid if self.proc else None,
                "running":       alive,
                "started_at":    self.started_at,
                "stopped_at":    self.stopped_at,
                "exit_code":     self.exit_code,
                "uptime_sec":    (time.time() - self.started_at) if alive else 0,
                "missing_env":   missing_env,
                "missing_import": missing_imp,
                "log_lines":     len(self.log),
                "ready_to_start": (not missing_env and not missing_imp) and not alive,
                "cmd":           list(self.meta.get("cmd", [])),
                # Reasoning-mode introspection — None for bots that
                # don't have a mode toggle (mcp-sse, agentmod).
                "mode_env":      mode_env,
                "mode":          (current_mode or mode_default) if mode_env else None,
                "mode_options":  ["fast", "mas"] if mode_env else None,
            }

    def tail(self, n: int = 50) -> List[str]:
        with self._lock:
            return list(self.log)[-int(n):]

    # ---- start / stop ------------------------------------------------

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None:
                return {"ok": False, "message": "already running",
                        "pid": self.proc.pid}
            # Re-check preconditions every start.
            missing_env = [k for k in self.meta.get("requires_env", [])
                           if not os.environ.get(k)]
            if missing_env:
                return {"ok": False,
                        "message": f"missing env vars: {missing_env}",
                        "missing_env": missing_env}
            missing_imp = [m for m in self.meta.get("requires_import", [])
                           if not _can_import(m)]
            if missing_imp:
                return {"ok": False,
                        "message": f"missing optional Python deps: {missing_imp}",
                        "missing_import": missing_imp,
                        "hint": f"agcl deploy {self.meta.get('wizard') or self.name}"}

            env = os.environ.copy()
            # Avoid coloured ANSI noise in log buffer.
            env.setdefault("NO_COLOR", "1")
            env.setdefault("PYTHONUNBUFFERED", "1")

            try:
                self.proc = subprocess.Popen(
                    self.meta["cmd"],
                    cwd=str(PROJECT_ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as e:
                return {"ok": False, "message": f"spawn failed: {e}"}

            self.started_at = time.time()
            self.stopped_at = 0.0
            self.exit_code = None
            self.log.append(f"[bot_runtime] started pid={self.proc.pid} cmd={' '.join(self.meta['cmd'])}")
            # Background reader thread — drains stdout into the deque.
            self._reader = threading.Thread(
                target=self._read_output, name=f"bot-{self.name}-log", daemon=True)
            self._reader.start()
            return {"ok": True, "pid": self.proc.pid}

    def stop(self, grace_sec: float = 5.0) -> Dict[str, Any]:
        with self._lock:
            if self.proc is None or self.proc.poll() is not None:
                return {"ok": True, "message": "not running"}
            pid = self.proc.pid
            try:
                # SIGTERM first — discord.py / slack-bolt handle it gracefully.
                self.proc.terminate()
            except Exception as e:
                return {"ok": False, "message": f"terminate failed: {e}"}
        # Wait outside the lock so status() / tail() keep working.
        try:
            self.proc.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            # Escalate to SIGKILL.
            try: self.proc.kill()
            except Exception: pass
            try: self.proc.wait(timeout=2.0)
            except Exception: pass
        with self._lock:
            self.exit_code = self.proc.returncode if self.proc else None
            self.stopped_at = time.time()
            self.log.append(f"[bot_runtime] stopped pid={pid} exit_code={self.exit_code}")
        return {"ok": True, "pid": pid, "exit_code": self.exit_code}

    def restart(self, grace_sec: float = 5.0) -> Dict[str, Any]:
        self.stop(grace_sec=grace_sec)
        return self.start()

    def set_mode(self, mode: str, auto_restart: bool = True) -> Dict[str, Any]:
        """Flip the bot's reasoning mode (fast | mas).

        Persists to .env via the same patcher the wizard system uses
        and updates live os.environ. If the bot is currently running,
        we auto-restart so the subprocess inherits the new env (env is
        captured at fork)."""
        mode_env = self.meta.get("mode_env")
        if not mode_env:
            return {"ok": False, "message": "this bot has no mode toggle"}
        m = (mode or "").lower().strip()
        opts = ("fast", "mas")
        if m not in opts:
            return {"ok": False, "message": f"mode must be one of {opts}"}
        from agcl.setup import _patch_env_file
        _patch_env_file({mode_env: m})
        os.environ[mode_env] = m
        was_running = self.is_alive()
        if was_running and auto_restart:
            self.restart()
        return {"ok": True, "mode": m, "env_var": mode_env,
                "restarted": was_running and auto_restart,
                "status": self.status()}

    # ---- internal ----------------------------------------------------

    def _read_output(self) -> None:
        """Drain stdout into our rolling log. One line per appendleft."""
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for line in iter(self.proc.stdout.readline, ""):
                line = line.rstrip("\n")
                with self._lock:
                    self.log.append(line)
        except Exception as e:
            with self._lock:
                self.log.append(f"[bot_runtime] reader exited: {e}")


# ============================================================
# Registry (singleton)
# ============================================================

_BOTS: Dict[str, ManagedBot] = {}
_REG_LOCK = threading.Lock()


def get_bot(name: str) -> Optional[ManagedBot]:
    with _REG_LOCK:
        if name not in BOTS:
            return None
        if name not in _BOTS:
            _BOTS[name] = ManagedBot(name, BOTS[name])
        return _BOTS[name]


def list_all() -> List[Dict[str, Any]]:
    return [get_bot(n).status() for n in BOTS]


def stop_all(grace_sec: float = 5.0) -> List[Dict[str, Any]]:
    """Best-effort stop. Called on node shutdown — see node.py lifespan."""
    out = []
    with _REG_LOCK:
        bots = list(_BOTS.values())
    for b in bots:
        try:
            out.append({"name": b.name, **b.stop(grace_sec=grace_sec)})
        except Exception as e:
            out.append({"name": b.name, "ok": False, "error": str(e)})
    return out


# ============================================================
# Helpers
# ============================================================

def _can_import(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False
