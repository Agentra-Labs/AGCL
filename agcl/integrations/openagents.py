"""
OpenAgents (openagents.org) integration.

Wraps every AGCL manifest tool as an OpenAgents AgentMod whose
`on_message` and `on_task` hooks delegate to AGCL. Drop the resulting
Python file into an OpenAgents network and the AGCL engine becomes a
participant: messages route through `agcl.mas.run` by default, and
explicit task names can target any tool by id.

Usage:
    pip install openagents[sdk]
    python main.py agentmod                         # interactive run
    python main.py agentmod --workspace ws-id       # join a workspace

The OpenAgents SDK is imported lazily; install it on demand.
"""

from __future__ import annotations

import sys
from typing import Any, Dict

from .manifest import TOOLS, call_tool


SDK_HINT = (
    "OpenAgents SDK not installed. Install with:\n"
    "    pip install openagents[sdk]\n"
    "and re-run."
)


def _build_mod_class():
    try:
        from openagents import AgentMod              # type: ignore
    except Exception:
        return None

    class AGCLMod(AgentMod):
        """AGCL participant in an OpenAgents network."""

        name = "agcl"
        description = "Agentic CLI - recursive MAS, mini-model, plugin host"

        def on_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
            text = (message.get("text")
                     or message.get("content")
                     or "").strip()
            if not text:
                return {"text": ""}
            res = call_tool("agcl.mas.run", {"message": text})
            return {"text": res.get("answer", ""), "topic_id": res.get("topic_id")}

        def on_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
            tool = task.get("tool") or task.get("name")
            args = task.get("args") or task.get("arguments") or {}
            if not tool:
                return {"error": "task missing 'tool' name"}
            try:
                return {"ok": True, "result": call_tool(tool, args)}
            except KeyError:
                return {"error": f"unknown tool {tool!r}",
                        "available": [t["name"] for t in TOOLS]}
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}

    return AGCLMod


def run(workspace: str = None) -> int:
    cls = _build_mod_class()
    if cls is None:
        print(SDK_HINT, file=sys.stderr)
        return 1
    mod = cls()
    print(f"AGCL OpenAgents mod ready ({len(TOOLS)} tools)")
    print("  on_message  -> agcl.mas.run")
    print("  on_task     -> any of:")
    for t in TOOLS:
        print(f"                 {t['name']}")
    if workspace is None:
        print("  (no --workspace given; mod is instantiated but not joined)")
        return 0
    # Different OpenAgents SDK versions expose different join methods;
    # we try the documented ones in order without making assumptions.
    join = (getattr(mod, "join", None)
             or getattr(mod, "connect", None)
             or getattr(mod, "run", None))
    if join is None:
        print("ERROR: this OpenAgents SDK build doesn't expose join()/connect()/run() on AgentMod;",
              file=sys.stderr)
        print("       drop the AGCLMod class into your network manually.", file=sys.stderr)
        return 1
    join(workspace)
    return 0


# Import-time export so users can `from agcl.integrations.openagents import AGCLMod`
AGCLMod = _build_mod_class()
