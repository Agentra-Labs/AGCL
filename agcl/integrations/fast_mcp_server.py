"""
FastMCP adapter (https://github.com/jlowin/fastmcp).

FastMCP is a higher-level wrapper over the MCP SDK. It uses Python
type hints + decorators to register tools instead of the plain
`@server.list_tools()` / `@server.call_tool()` pattern. Single-file
servers, faster iteration, identical wire protocol.

We expose the same TOOLS registry both ways so MCP clients see the
identical surface regardless of which transport the user runs.

Usage:
    pip install fastmcp
    python main.py mcp --fast            # FastMCP stdio
    python main.py mcp --fast --transport sse --port 8765
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict

from .manifest import TOOLS, call_tool


SDK_HINT = (
    "FastMCP not installed. Install with:\n"
    "    pip install fastmcp\n"
    "or fall back to the stdio MCP server: `python main.py mcp` (without --fast)."
)


def _serialize(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, indent=2, default=str)
    except Exception:
        return repr(result)


def _bind_tools(mcp: Any) -> None:
    """Register every entry in TOOLS as a fastmcp.tool."""
    for spec in TOOLS:
        name        = spec["name"]
        description = spec["description"]
        schema      = spec.get("schema", {})

        # FastMCP exposes a `.tool` decorator — we use the lower-level
        # `add_tool` API so we can register schemas dynamically (the
        # decorator path requires Python type hints on a real function).
        async def _impl(arguments: Dict[str, Any] | None = None,
                         _name: str = name) -> str:
            try:
                result = await asyncio.to_thread(call_tool, _name, arguments or {})
                return _serialize(result)
            except Exception as e:
                return f"error: {type(e).__name__}: {e}"

        mcp.add_tool(
            fn=_impl,
            name=name,
            description=description,
            input_schema=schema,
        )


def run_stdio() -> int:
    try:
        from fastmcp import FastMCP   # type: ignore
    except ImportError:
        print(SDK_HINT, file=sys.stderr)
        return 1

    mcp = FastMCP("agcl")
    _bind_tools(mcp)
    # FastMCP runs stdio by default.
    mcp.run()
    return 0


def run_sse(host: str = "127.0.0.1", port: int = 8765) -> int:
    try:
        from fastmcp import FastMCP   # type: ignore
    except ImportError:
        print(SDK_HINT, file=sys.stderr)
        return 1

    mcp = FastMCP("agcl")
    _bind_tools(mcp)
    # FastMCP picks up host/port via its run() args.
    mcp.run(transport="sse", host=host, port=port)
    return 0
