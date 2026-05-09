"""
Model Context Protocol (MCP) server for AGCL.

MCP is the de-facto plug-in standard across Claude Code, Cursor,
Copilot Studio, OpenAI Agents SDK, LangChain, CrewAI, AutoGen, and
the rest. Build this server once and AGCL is reachable from all of
them. See docs/integrations.md.

Usage:
    pip install mcp
    python main.py mcp                  # stdio (Claude Desktop / Cursor)
    python main.py mcp --transport sse  # HTTP SSE (any MCP HTTP client)

We import the `mcp` SDK lazily so the AGCL package itself doesn't
depend on it. If the SDK isn't installed we print a one-line install
hint and exit cleanly.

Design:
    The whole tool list comes from agcl.integrations.manifest.TOOLS so
    every external surface stays in lockstep. New AGCL operations show
    up in MCP automatically the moment they're added to the manifest.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict

from .manifest import TOOLS, call_tool


SDK_HINT = (
    "MCP SDK not installed. Install with:\n"
    "    pip install mcp\n"
    "and re-run."
)


def _serialize(result: Any) -> str:
    """Tools may return dicts/lists; MCP wants a string per content block."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, indent=2, default=str)
    except Exception:
        return repr(result)


def run_stdio() -> int:
    """Start the MCP server speaking JSON-RPC over stdio (the default
    Claude Desktop / Cursor expect)."""
    try:
        from mcp.server import Server                   # type: ignore
        from mcp.server.stdio import stdio_server       # type: ignore
        from mcp.types import Tool, TextContent          # type: ignore
    except Exception:
        print(SDK_HINT, file=sys.stderr)
        return 1

    server = Server("agcl")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(name=t["name"], description=t["description"],
                 inputSchema=t["schema"])
            for t in TOOLS
        ]

    @server.call_tool()
    async def call(name: str, arguments: Dict[str, Any]):
        try:
            result = await asyncio.to_thread(call_tool, name, arguments)
        except Exception as e:
            return [TextContent(type="text",
                                 text=f"error: {type(e).__name__}: {e}")]
        return [TextContent(type="text", text=_serialize(result))]

    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())
    return 0


def run_sse(host: str = "127.0.0.1", port: int = 8765) -> int:
    """Start an HTTP MCP server using SSE transport (one of two MCP
    HTTP transports). Pair with any MCP HTTP client (`mcp connect
    http://host:port/sse`)."""
    try:
        from mcp.server import Server                       # type: ignore
        from mcp.server.sse import SseServerTransport       # type: ignore
        from mcp.types import Tool, TextContent              # type: ignore
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
    except Exception:
        print(SDK_HINT, file=sys.stderr)
        return 1

    server = Server("agcl")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(name=t["name"], description=t["description"],
                 inputSchema=t["schema"])
            for t in TOOLS
        ]

    @server.call_tool()
    async def call(name: str, arguments: Dict[str, Any]):
        try:
            result = await asyncio.to_thread(call_tool, name, arguments)
        except Exception as e:
            return [TextContent(type="text",
                                 text=f"error: {type(e).__name__}: {e}")]
        return [TextContent(type="text", text=_serialize(result))]

    transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with transport.connect_sse(
                request.scope, request.receive, request._send) as (read, write):
            await server.run(read, write, server.create_initialization_options())

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=transport.handle_post_message),
    ])
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def dump_manifest() -> int:
    """Print the tool manifest as JSON (no SDK required). Useful for
    debugging or for static distribution to platforms that accept a
    JSON tool spec directly."""
    payload = {
        "name":    "agcl",
        "version": "1",
        "tools": [
            {"name": t["name"], "description": t["description"],
             "inputSchema": t["schema"]}
            for t in TOOLS
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0
