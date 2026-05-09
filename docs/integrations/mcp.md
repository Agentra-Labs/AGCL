# MCP server — the universal adapter

<p align="left">
  <a href="https://modelcontextprotocol.io/"><img alt="MCP" src="https://img.shields.io/badge/Model%20Context%20Protocol-1.x-000000"></a>
  <a href="https://img.shields.io/badge/transport-stdio%20%7C%20SSE-blue"><img alt="Transport" src="https://img.shields.io/badge/transport-stdio%20%7C%20SSE-blue"></a>
  <a href="https://img.shields.io/badge/FastMCP-supported-000000"><img alt="FastMCP" src="https://img.shields.io/badge/FastMCP-supported-000000"></a>
</p>

One MCP server covers Claude Desktop, Claude Code, Cursor, OpenAI
Agents SDK, LangChain / LangGraph, CrewAI, AutoGen, Copilot Studio,
Cloudflare Agents, and Vercel AI SDK. Build this first.

> Part of the **[Agent-platform integrations](../integrations.md)**.
> Source: [`agcl/integrations/mcp_server.py`](../../agcl/integrations/mcp_server.py)
> + [`agcl/integrations/fast_mcp_server.py`](../../agcl/integrations/fast_mcp_server.py).

---

## Install

```bash
pip install mcp                  # the official MCP Python SDK
# or
pip install fastmcp              # higher-level wrapper; opt-in via --fast
```

The two are interchangeable — same wire protocol, same tool surface.
FastMCP is more ergonomic for adding new tools; the official SDK has
broader compatibility with edge clients.

---

## stdio transport (Claude Desktop, Cursor, Claude Code)

```bash
python main.py mcp                    # official mcp SDK
python main.py mcp --fast             # FastMCP path
```

In `~/.config/claude/claude_desktop_config.json` (or your client's
equivalent):

```json
{
  "mcpServers": {
    "agcl": {
      "command": "python",
      "args":    ["/path/to/AGCL/main.py", "mcp"]
    }
  }
}
```

---

## HTTP SSE transport (any MCP HTTP client, Copilot Studio, etc.)

```bash
python main.py mcp --transport sse --host 127.0.0.1 --port 8765
python main.py mcp --fast --transport sse --port 8765
```

Pair with any MCP HTTP client; the SSE endpoint is `/sse` and message
posts go to `/messages/`.

---

## Static manifest dump

No SDK needed — useful for inspection or distribution to platforms
that consume a static JSON spec:

```bash
python main.py mcp --transport manifest > agcl.mcp.json
```

The manifest follows the MCP tool spec:

```json
{
  "name":    "agcl",
  "version": "1",
  "tools": [
    {
      "name":        "agcl.mas.run",
      "description": "Run a turn through AGCL's recursive multi-agent engine. ...",
      "inputSchema": { "type": "object", "properties": { ... }, "required": [...] }
    },
    ...
  ]
}
```

Same artifact, every consumer.

---

## Tools surfaced

Pulled directly from
[`agcl/integrations/manifest.py`](../../agcl/integrations/manifest.py).
Adding to that file makes the tool show up here automatically.

| Tool | Purpose |
|---|---|
| `agcl.mas.run`             | Run one turn through the recursive MAS engine |
| `agcl.mas.status`          | Inspect training-control state of a session |
| `agcl.mas.pause`           | Pause training mid-run (resumable) |
| `agcl.mas.resume`          | Resume a paused session |
| `agcl.mas.halt`            | Halt training cleanly |
| `agcl.mas.latent`          | Snapshot the latest loop latent (truncated) |
| `agcl.mini.test`           | Generate from the background mini-model |
| `agcl.mini.status`         | Mini-trainer status |
| `agcl.mini.start` / `stop` | Toggle the background trainer |
| `agcl.topics.list`         | List saved trained-topic checkpoints |
| `agcl.plugins.list`        | List discovered AGCL plugins |
| `agcl.toolkit.discover`    | Snapshot which infra adapters are configured + importable |
| `agcl.toolkit.ping`        | Reachability check against one toolkit adapter |
| `agcl.toolkit.chat`        | Chat through whichever inference gateway is configured |
| `agcl.toolkit.emit_docker` | Generate Dockerfile + Compose for the current config |
| `agcl.toolkit.emit_k8s`    | Generate Helm chart + standalone K8s manifests |
| `agcl.toolkit.emit_gcp`    | Generate Cloud Run service.yaml + Cloud Build pipeline |

Plus any tool a plugin registers. Plugins extend the registry via:

```python
# core path
from agcl.integrations.manifest import register_tool
register_tool(name=..., description=..., schema=..., handler=...)

# from a plugin's register(ctx) callback
ctx.add_tool(name=..., description=..., schema=..., handler=...)
```

The shipped Multica plugin
([`plugins/multica.py`](../../plugins/multica.py)) registers
`agcl.multica.run` this way — it appears in MCP automatically when the
plugin loads.

See [../integrations.md#adding-a-tool](../integrations.md#adding-a-new-tool-to-all-platforms-at-once).
