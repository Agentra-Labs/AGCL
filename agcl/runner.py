"""
Headless task runner — `python main.py run --task ... --workdir ... --output jsonl`.

This is the contract every external orchestrator (Multica, GCP Cloud
Run jobs, GitHub Actions, generic shell scripts) calls when it wants
AGCL to execute one bounded task and stream structured progress back.

Output formats:
    --output jsonl    one JSON object per line on stdout (default for headless)
    --output text     plain text (the legacy `recursive run` shape)
    --output sse      `data: {...}` lines, suitable for piping into a relay

Event taxonomy (jsonl):
    {"type": "status",      "message": "..."}
    {"type": "thinking",    "content": "...", "stage": "stage1|stage2|gen"}
    {"type": "tool_call",   "name": "...", "input": {...}}
    {"type": "tool_result", "name": "...", "output": "..."}
    {"type": "text",        "content": "..."}              # final answer chunks
    {"type": "answer",      "content": "...", "topic_id": "..."}   # full answer
    {"type": "error",       "message": "...", "etype": "..."}
    {"type": "done",        "status": "completed|failed|blocked|cancelled",
     "session_id": "...", "topic_id": "..."}

Session resume:
    --session-id <id>   reuses .agent_state/sess_<id>.json + topic links
    --workdir <dir>     overrides STATE_DIR for this run (orchestrator-friendly)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _emit(fmt: str, payload: Dict[str, Any]) -> None:
    if fmt == "jsonl":
        sys.stdout.write(json.dumps(payload, default=str) + "\n")
    elif fmt == "sse":
        sys.stdout.write(f"data: {json.dumps(payload, default=str)}\n\n")
    else:  # text
        t = payload.get("type", "?")
        if t == "text":
            sys.stdout.write(payload.get("content", ""))
        elif t == "status":
            sys.stdout.write(f"[status] {payload.get('message','')}\n")
        elif t == "error":
            sys.stdout.write(f"[error] {payload.get('message','')}\n")
        elif t == "answer":
            sys.stdout.write(payload.get("content", "") + "\n")
        elif t == "done":
            sys.stdout.write(f"[done] {payload.get('status','')}\n")
        # tool_*/thinking/log are silent in text mode.
    sys.stdout.flush()


def _setup_workdir(workdir: Optional[str]) -> None:
    """Point STATE_DIR at <workdir>/.agcl so each task is isolated."""
    if not workdir:
        return
    p = Path(workdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    state = p / ".agcl"
    state.mkdir(parents=True, exist_ok=True)
    os.environ["STATE_DIR"] = str(state)
    # Clear any cached config so the new STATE_DIR takes effect.
    import importlib
    import agcl.config as cfg
    importlib.reload(cfg)


def run(task: str, *,
        session_id: str = "default",
        workdir: Optional[str] = None,
        output: str = "jsonl",
        provider: Optional[str] = None,
        force_cloud: bool = False,
        force_continue: bool = False,
        skill_files: Optional[list] = None,
        max_new_tokens: int = 256,
        ) -> int:
    """
    Execute one task. Returns 0 on success, non-zero on failure.

    skill_files: optional list of paths to markdown skill bundles that
    will be prepended to the task as system context. Used by Multica's
    skill injection pattern.
    """
    _setup_workdir(workdir)

    # Read skill content (if any) and prepend as context.
    context_blocks = []
    for sf in skill_files or []:
        try:
            content = Path(sf).read_text()
            context_blocks.append(content)
        except OSError as e:
            _emit(output, {"type": "log",
                            "message": f"skill {sf!r} unreadable: {e}"})

    full_task = task
    if context_blocks:
        full_task = "\n\n---\n\n".join(context_blocks) + "\n\n---\n\n" + task

    started = time.time()
    _emit(output, {"type": "status", "message": "starting",
                    "session_id": session_id, "started_at": started})

    # Build a session and stream the turn. We forward auto-train + generation
    # events into the chosen output format.
    async def _run_async() -> Dict[str, Any]:
        from agcl.recursive import build_from_config, RecursiveSession

        _emit(output, {"type": "status", "message": "building MAS"})
        mas = build_from_config()

        sess = RecursiveSession(
            mas, verbose=False, provider=provider,
            max_new_tokens=max_new_tokens,
        )

        def on_progress(ev: Dict[str, Any]) -> None:
            mapped = _map_session_event(ev)
            if mapped is not None:
                _emit(output, mapped)

        try:
            answer = await sess.turn(
                full_task,
                force_cloud=force_cloud,
                force_continue=force_continue,
                on_progress=on_progress,
            )
        except Exception as exc:
            from agcl.recursive.control import HaltedError
            if isinstance(exc, HaltedError):
                return {"status": "cancelled", "error": str(exc)}
            return {"status": "failed", "error": str(exc),
                    "etype": type(exc).__name__}

        return {
            "status": "completed",
            "answer": answer,
            "session_id": session_id,
            "topic_id":   sess.current_topic_id,
        }

    result = asyncio.run(_run_async())

    if "answer" in result:
        _emit(output, {"type": "answer", "content": result["answer"],
                        "session_id": session_id,
                        "topic_id": result.get("topic_id")})
    if "error" in result:
        _emit(output, {"type": "error", "message": result["error"],
                        "etype": result.get("etype", "Error")})

    _emit(output, {"type": "done",
                    "status": result["status"],
                    "session_id": session_id,
                    "topic_id":   result.get("topic_id"),
                    "elapsed_sec": round(time.time() - started, 3)})
    return 0 if result["status"] == "completed" else 1


def _map_session_event(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Translate the session's internal event names into the unified taxonomy
    that Multica + orchestrators consume.
    """
    e = ev.get("event", "")
    if e in ("topic_gate", "topic_switch_confirmed"):
        return {"type": "thinking", "stage": "topic-gate",
                "content": json.dumps({k: v for k, v in ev.items() if k != "event"})}
    if e in ("retrieval_lookup", "retrieval_hit", "retrieval_miss"):
        return {"type": "status", "message": e,
                "meta": {k: v for k, v in ev.items() if k != "event"}}
    if e == "bootstrap_start":
        return {"type": "status", "message": "bootstrapping from cloud teacher"}
    if e == "bootstrap_done":
        return {"type": "status", "message": "bootstrap done",
                "meta": {"chars": ev.get("answer_chars"),
                          "n_reformulations": ev.get("n_reformulations")}}
    if e in ("stage1_start", "stage1_step", "stage1_done"):
        return {"type": "thinking", "stage": "stage1",
                "content": _format_stage(e, ev)}
    if e in ("stage2_start", "stage2_step", "stage2_done", "stage2_skipped"):
        return {"type": "thinking", "stage": "stage2",
                "content": _format_stage(e, ev)}
    if e == "topic_persisted":
        return {"type": "status", "message": "topic persisted",
                "meta": {"topic_id": ev.get("topic_id")}}
    if e == "topic_persist_failed":
        return {"type": "log", "message": f"topic persist failed: {ev.get('error')}"}
    if e == "generation_start":
        return {"type": "status", "message": f"generating ({ev.get('mode')})"}
    if e == "prefix":
        return {"type": "text", "content": ev.get("text", "")}
    if e == "fallback_to_cloud":
        return {"type": "status", "message": "local degenerate; falling back to cloud"}
    if e == "answer":
        # Full answer comes via the run() path; keep this silent.
        return None
    if e == "error":
        return {"type": "error", "message": ev.get("message", ""),
                "etype": ev.get("type", "Error")}
    return None


def _format_stage(name: str, ev: Dict[str, Any]) -> str:
    if name.endswith("_step"):
        return f"step {ev.get('step')}/{ev.get('total_steps')} loss={ev.get('loss'):.4f}"
    if name.endswith("_done"):
        return f"first={ev.get('first_loss')} final={ev.get('final_loss')}"
    if name.endswith("_skipped"):
        return f"skipped: {ev.get('reason')}"
    if name.endswith("_start"):
        return f"start total_steps={ev.get('total_steps')}"
    return name
