"""
Multica.ai integration plugin.

Multica (https://github.com/multica-ai/multica) is a Linear-style task
control plane that delegates execution to whichever agent CLI the user
has on PATH. From AGCL's side, the integration is mostly about being
a well-behaved CLI:

    - Accept --workdir, --resume <session-id>, --output jsonl
    - Emit events in the unified taxonomy (text / thinking / tool_call /
      tool_result / status / log / error / done)
    - Read injected skills from <workdir>/.agcl/context.md or
      .agcl/skills/*.md before starting

This plugin makes the binding visible at runtime — it adds:

    /node/multica/health      -> {"agcl": "ready", "session_dir": ...}
    /node/multica/skills      -> read injected skills under the workdir
    /node/multica/run         -> POST a task; returns the same jsonl events
                                 as `agcl run`, served as SSE for live UIs

Plus a CLI command `multica` for `python main.py shell` users:

    > multica /path/to/workdir "task description"

Drop this file into plugins/ and the routes appear under /node/multica/*.

Multica's Go-side wiring (server/pkg/agent/agcl/agcl.go) is documented
in docs/integrations/multica.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def _load_skills(workdir: str) -> List[Dict[str, str]]:
    """
    Multica writes injected skills to <workdir>/.agcl/context.md OR a
    set of .agcl/skills/*.md files. Either layout works.
    """
    p = Path(workdir).resolve() / ".agcl"
    out: List[Dict[str, str]] = []
    ctx = p / "context.md"
    if ctx.exists():
        out.append({"name": "context.md", "content": ctx.read_text()})
    skills_dir = p / "skills"
    if skills_dir.is_dir():
        for f in sorted(skills_dir.glob("*.md")):
            out.append({"name": f.name, "content": f.read_text()})
    return out


def register(ctx) -> None:
    # ---------------- declare config so config-bundle hints work --------
    ctx.declare_config(
        env_vars=[
            {"name": "MULTICA_SERVER_URL", "secret": False,
             "doc": "Multica daemon endpoint (default: ws://localhost:8080/ws)"},
            {"name": "MULTICA_TOKEN", "secret": True,
             "doc": "Multica Personal Access Token (Bearer)"},
            {"name": "MULTICA_WORKSPACE_ID", "secret": False,
             "doc": "Multica workspace UUID"},
        ],
        files=["mas.json"],
    )

    # ---------------- HTTP routes under /node/multica/* -----------------
    @ctx.router.get("/multica/health")
    def multica_health() -> Dict[str, Any]:
        return {
            "agcl":            "ready",
            "session_dir":     os.environ.get("STATE_DIR", ".agent_state"),
            "supports_resume": True,
            "output_formats":  ["jsonl", "sse", "text"],
            "event_taxonomy":  ["text", "thinking", "tool_call",
                                 "tool_result", "status", "log", "error", "done"],
        }

    @ctx.router.get("/multica/skills")
    def multica_skills(workdir: str = ".") -> Dict[str, Any]:
        return {"workdir": workdir, "skills": _load_skills(workdir)}

    @ctx.router.post("/multica/run")
    async def multica_run(body: Dict[str, Any]) -> Any:
        """
        Run a Multica-issued task, streaming AGCL's jsonl event taxonomy
        as SSE. Body shape (from Multica's `Task` struct):

            {"id": "...", "title": "...", "description": "...",
             "workdir": "/path", "session_id": "...",
             "skills": [{"name":"...","content":"..."}],
             "env":     {"K": "V"}}
        """
        from fastapi.responses import StreamingResponse
        from agcl.runner import run as runner_run

        task = (body.get("description") or body.get("title") or "").strip()
        if not task:
            return {"error": "title or description required"}
        workdir   = body.get("workdir") or "."
        sid       = body.get("session_id") or body.get("id") or "default"

        # Inline-injected skills get prepended to the task as context.
        skills_inline = body.get("skills") or []
        for s in skills_inline:
            if s.get("content"):
                task = f"## {s.get('name','skill')}\n\n{s['content']}\n\n---\n\n{task}"

        for k, v in (body.get("env") or {}).items():
            os.environ[str(k)] = str(v)

        async def gen():
            # runner_run writes jsonl to stdout; we capture by overriding
            # the emit function via os.dup2 isn't worth it — instead, run
            # in a thread and tee through a queue.
            queue: asyncio.Queue = asyncio.Queue()
            DONE = object()

            class _Tee:
                def write(self, s):
                    if s.strip():
                        try:
                            obj = json.loads(s.strip())
                        except json.JSONDecodeError:
                            return
                        loop.call_soon_threadsafe(queue.put_nowait, obj)
                def flush(self): pass

            loop = asyncio.get_running_loop()

            def _run_in_thread():
                old_stdout = sys.stdout
                try:
                    sys.stdout = _Tee()
                    try:
                        runner_run(
                            task,
                            session_id=sid, workdir=workdir, output="jsonl",
                            skill_files=None,
                        )
                    finally:
                        sys.stdout = old_stdout
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, DONE)

            asyncio.create_task(asyncio.to_thread(_run_in_thread))

            while True:
                ev = await queue.get()
                if ev is DONE:
                    break
                yield f"data: {json.dumps(ev)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---------------- TUI command -----------------------------------------
    @ctx.add_command("multica", help="run a Multica-style task: <workdir> \"<task>\"",
                       aliases=["mc"])
    def _cmd_multica(args: str) -> None:
        parts = args.strip().split(" ", 1)
        if len(parts) < 2:
            print("usage: multica <workdir> \"<task>\"")
            return
        workdir, task = parts[0], parts[1].strip().strip("\"'")
        from agcl.runner import run as runner_run
        runner_run(task, session_id="multica", workdir=workdir, output="text")

    # ---------------- expose as a tool in MCP/Slack/OpenAgents -----------
    def _tool_multica_run(arguments: Dict[str, Any]) -> Dict[str, Any]:
        from agcl.runner import run as runner_run
        task    = (arguments.get("task") or "").strip()
        workdir = arguments.get("workdir") or "."
        sid     = arguments.get("session_id") or "multica"
        if not task:
            return {"error": "task required"}
        # Capture jsonl by redirecting stdout to a list-collecting Tee.
        import io
        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            runner_run(task, session_id=sid, workdir=workdir, output="jsonl")
        finally:
            sys.stdout = old
        events = []
        for line in buf.getvalue().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # The last event is `done`; the answer is in the second-to-last.
        answer = next((e["content"] for e in events
                       if e.get("type") == "answer"), "")
        return {"events": events, "answer": answer, "session_id": sid}

    ctx.add_tool(
        name="agcl.multica.run",
        description="Run an AGCL task in the Multica jsonl protocol "
                    "(Multica Backend.Execute contract).",
        schema={
            "type": "object",
            "properties": {
                "task":       {"type": "string"},
                "workdir":    {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["task"],
        },
        handler=_tool_multica_run,
    )
