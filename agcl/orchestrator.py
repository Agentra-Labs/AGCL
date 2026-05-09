"""
Orchestrator — drive remote AGCL nodes from this CLI.

AGCL isn't only a recursive-MAS runtime; it's also a **local
orchestration plugin** that calls into self-hosted AGCL endpoints (your
own laptop, a teammate's box, a Cloud Run instance, a K8s pod) and
fans tasks out across them.

Two modes:

  1. One-shot:
       python main.py orchestrate --target http://host:9876 \\
                                   --auth-key $KEY --task "describe X"

  2. Playbook:
       python main.py orchestrate playbook.json
       (or .yaml — YAML is parsed via a tiny inline reader; PyYAML if present)

Playbook schema (JSON or YAML):

    {
      "targets": [
        {"name": "laptop", "url": "http://localhost:9876", "auth_key": "..."},
        {"name": "cloud",  "url": "https://agcl.example.com", "auth_key_env": "PROD_KEY"}
      ],
      "tasks": [
        {"target": "laptop", "task": "summarize the diff", "session_id": "diff-1"},
        {"target": "cloud",  "task": "do the heavy retrieval"}
      ]
    }
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


def _emit(fmt: str, payload: Dict[str, Any]) -> None:
    if fmt == "jsonl":
        sys.stdout.write(json.dumps(payload, default=str) + "\n")
    else:  # text
        if payload.get("type") == "answer":
            sys.stdout.write(payload.get("content", "") + "\n")
        elif payload.get("type") == "error":
            sys.stdout.write(f"[error] {payload.get('message','')}\n")
        elif payload.get("type") == "status":
            sys.stdout.write(f"[{payload.get('target','')}] {payload.get('message','')}\n")
    sys.stdout.flush()


def _load_playbook(path: str) -> Dict[str, Any]:
    p = Path(path)
    raw = p.read_text()
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
            return yaml.safe_load(raw)
        except ImportError:
            sys.stderr.write(
                "PyYAML not installed; install with `pip install pyyaml` or "
                "use a .json playbook.\n"
            )
            sys.exit(2)
    return json.loads(raw)


def _resolve_auth(target: Dict[str, Any]) -> str:
    if target.get("auth_key"):
        return str(target["auth_key"])
    env = target.get("auth_key_env")
    if env and os.environ.get(env):
        return os.environ[env]
    if os.environ.get("AGCL_TARGET_KEY"):
        return os.environ["AGCL_TARGET_KEY"]
    raise RuntimeError(
        f"target {target.get('name', target.get('url'))!r}: "
        f"no auth_key / auth_key_env / AGCL_TARGET_KEY")


async def _stream_one(target: Dict[str, Any], task: Dict[str, Any],
                       fmt: str) -> Dict[str, Any]:
    """Run a single task on a single target and emit each SSE event."""
    name = target.get("name", target.get("url", "?"))
    url = target["url"].rstrip("/")
    auth = _resolve_auth(target)

    body = {"message": task["task"],
            "session_id": task.get("session_id", "default")}
    for k in ("force_cloud", "force_continue", "max_new_tokens",
              "cloud_provider"):
        if k in task:
            body[k] = task[k]

    _emit(fmt, {"type": "status", "target": name, "message": "starting",
                 "url": url})
    final: Dict[str, Any] = {"target": name, "ok": False}
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream(
                "POST", f"{url}/node/mas/stream",
                json=body,
                headers={"Authorization": f"Bearer {auth}",
                         "Content-Type": "application/json",
                         "Accept": "text/event-stream"},
            ) as r:
                if r.status_code >= 400:
                    err = await r.aread()
                    raise RuntimeError(
                        f"{r.status_code}: {err.decode('utf-8','replace')[:200]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    ev["target"] = name
                    if ev.get("event") == "answer":
                        final = {"target": name, "ok": True,
                                 "answer": ev.get("text", ""),
                                 "topic_id": ev.get("topic_id")}
                        _emit(fmt, {"type": "answer", "target": name,
                                     "content": ev.get("text", ""),
                                     "topic_id": ev.get("topic_id")})
                    elif ev.get("event") == "error":
                        final = {"target": name, "ok": False,
                                 "error": ev.get("message", "")}
                        _emit(fmt, {"type": "error", "target": name,
                                     "message": ev.get("message", "")})
                    elif ev.get("event") == "done":
                        _emit(fmt, {"type": "status", "target": name,
                                     "message": "done"})
                    elif fmt == "jsonl":
                        _emit(fmt, ev)
    except Exception as e:
        final = {"target": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
        _emit(fmt, {"type": "error", "target": name,
                     "message": final["error"]})
    return final


async def _ping_one(target: Dict[str, Any]) -> Dict[str, Any]:
    """Cheap reachability check before fanning real work out."""
    name = target.get("name", target.get("url", "?"))
    url = target["url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{url}/node/health")
        return {"name": name, "ok": r.status_code == 200,
                "status": r.status_code, "url": url}
    except httpx.HTTPError as e:
        return {"name": name, "ok": False, "url": url, "error": str(e)}


def run(playbook: Optional[str] = None,
        target: Optional[str] = None,
        auth_key: Optional[str] = None,
        task: Optional[str] = None,
        output: str = "jsonl",
        parallel: int = 1) -> int:
    """
    Entry point for `python main.py orchestrate`.
    Returns 0 if all tasks succeeded, 1 otherwise.
    """
    # ---- one-shot mode ----
    if playbook is None:
        if not target or not task:
            sys.stderr.write(
                "usage: agcl orchestrate --target <url> --task '<msg>' "
                "[--auth-key KEY]   OR   agcl orchestrate <playbook.json>\n"
            )
            return 2
        spec = {"targets": [{"name": "target", "url": target,
                              "auth_key": auth_key}],
                "tasks":   [{"target": "target", "task": task}]}
    else:
        spec = _load_playbook(playbook)

    targets = {t["name"]: t for t in spec.get("targets", [])
                if "name" in t and "url" in t}
    if not targets:
        sys.stderr.write("playbook has no targets\n")
        return 2
    tasks: List[Dict[str, Any]] = list(spec.get("tasks", []))
    if not tasks:
        sys.stderr.write("playbook has no tasks\n")
        return 2

    async def _runner() -> List[Dict[str, Any]]:
        # Ping first so a stale target fails fast.
        pings = await asyncio.gather(*[_ping_one(t) for t in targets.values()])
        for p in pings:
            _emit(output, {"type": "status", "target": p.get("name"),
                            "message": "ping " + ("ok" if p["ok"] else "failed"),
                            "meta": p})

        sem = asyncio.Semaphore(max(1, parallel))

        async def _do(t: Dict[str, Any]) -> Dict[str, Any]:
            tgt_name = t["target"]
            if tgt_name not in targets:
                err = {"target": tgt_name, "ok": False,
                       "error": f"unknown target {tgt_name!r}"}
                _emit(output, {"type": "error", **err,
                                "message": err["error"]})
                return err
            async with sem:
                return await _stream_one(targets[tgt_name], t, output)

        return await asyncio.gather(*[_do(t) for t in tasks])

    results = asyncio.run(_runner())
    failed = [r for r in results if not r.get("ok")]
    _emit(output, {"type": "summary",
                    "total": len(results),
                    "ok": len(results) - len(failed),
                    "failed": len(failed)})
    return 0 if not failed else 1
