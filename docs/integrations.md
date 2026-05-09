# Agent-platform & infra integrations

<p align="left">
  <img src="assets/anthropic.svg" width="22" alt="Anthropic" />&nbsp;
  <img src="assets/openai.svg" width="22" alt="OpenAI" />&nbsp;
  <img src="assets/huggingface.svg" width="22" alt="Hugging Face" />&nbsp;
  <img src="assets/slack.svg" width="22" alt="Slack" />&nbsp;
  <img src="assets/discord.svg" width="22" alt="Discord" />&nbsp;
  <img src="assets/docker.svg" width="22" alt="Docker" />&nbsp;
  <img src="assets/kubernetes.svg" width="22" alt="Kubernetes" />&nbsp;
  <img src="assets/npm.svg" width="22" alt="npm" />&nbsp;
  <img src="assets/cloudflare.svg" width="22" alt="Cloudflare" />&nbsp;
  <img src="assets/redis.svg" width="22" alt="Redis" />
</p>

This is the front door for plugging AGCL into the rest of the
ecosystem. Each row is one page; pick the surface you actually need.

> **Sister doc:** for wiring AGCL into a GUI / web frontend (the node
> server's HTTP+SSE contract), see **[gui.md](gui.md)**.

---

## What's actually shipping

AGCL is reachable through three concrete contracts. Pick whichever
matches the tool you're connecting:

| Contract | Reach it via | Doc |
|---|---|---|
| **HTTP + SSE** under `/node/*` (auth: bearer) | `python main.py node` | [gui.md](gui.md) |
| **JSONL on stdout** (event taxonomy: status / thinking / text / answer / done / error) | `python main.py run --output jsonl` | [headless-run](integrations/headless-run.md) |
| **MCP** (stdio, SSE, or FastMCP) — wraps the tool registry from [`agcl/integrations/manifest.py`](../agcl/integrations/manifest.py) | `python main.py mcp [--fast]` | [mcp.md](integrations/mcp.md) |

Every adapter (Slack, Discord, OpenAgents, OpenAPI, Multica) wraps one
of those three. Adding a new tool means one entry in `manifest.py` —
it surfaces in every adapter automatically.

---

## Agent-platform adapters

| Surface | Page |
|---|---|
| <img src="https://img.shields.io/badge/MCP-stdio%20%7C%20SSE%20%7C%20FastMCP-000000" alt="MCP" /> Universal — Claude / Cursor / OpenAI Agents / LangChain / CrewAI / AutoGen / Copilot / Vercel | **[mcp.md](integrations/mcp.md)** |
| <img src="assets/slack.svg" width="14" align="absmiddle" alt="Slack" />&nbsp;Slack AI Apps (Bolt for Python) | **[slack.md](integrations/slack.md)** |
| <img src="assets/discord.svg" width="14" align="absmiddle" alt="Discord" />&nbsp;Discord (slash commands + agent bus) | **[discord.md](integrations/discord.md)** |
| OpenAgents (openagents.org AgentMod) | **[openagents.md](integrations/openagents.md)** |
| <img src="https://img.shields.io/badge/OpenAPI-3.0-6BA539" alt="OpenAPI" /> Zapier / n8n / Vertex AI / Copilot Studio | **[openapi.md](integrations/openapi.md)** |
| Multica.ai task control plane (jsonl bridge + skill ctx) | **[multica.md](integrations/multica.md)** |
| Headless task runner — Cloud Run jobs, GitHub Actions, shell scripts | **[headless-run.md](integrations/headless-run.md)** |

---

## Packaging & runtime

| Surface | Page |
|---|---|
| <img src="assets/npm.svg" width="14" align="absmiddle" alt="npm" />&nbsp;npm — JavaScript / TypeScript client package | **[npm.md](integrations/npm.md)** |
| <img src="assets/docker.svg" width="14" align="absmiddle" alt="Docker" />&nbsp;Docker — single Dockerfile + Compose stack | **[docker.md](integrations/docker.md)** |

---

## Cloud / infra

The full cloud-stack story (provider gateway → inference → state →
storage → edge) lives under **[cloud.md](integrations/cloud.md)**.
Direct links:

| # | Topic | Page |
|---|---|---|
| 1 | LiteLLM — provider abstraction gateway | **[cloud/litellm.md](integrations/cloud/litellm.md)** |
| 2 | Ollama — local / LAN / remote inference | **[cloud/ollama.md](integrations/cloud/ollama.md)** |
| 3 | vLLM — high-throughput inference backend | **[cloud/vllm.md](integrations/cloud/vllm.md)** |
| 4 | Redis / Valkey — distributed session state | **[cloud/redis.md](integrations/cloud/redis.md)** |
| 5 | Kubernetes + Helm — infra packaging | **[cloud/k8s.md](integrations/cloud/k8s.md)** |
| 6 | S3-compatible persistence | **[cloud/s3.md](integrations/cloud/s3.md)** |
| 7 | Cloudflare Workers + Durable Objects (edge relay) | **[cloud/cloudflare.md](integrations/cloud/cloudflare.md)** |
| 8 | HuggingFace TGI (legacy / HF Endpoints) | **[cloud/tgi.md](integrations/cloud/tgi.md)** |
| 9 | WebRTC node transport (experimental) | **[cloud/webrtc.md](integrations/cloud/webrtc.md)** |
| 10 | GCP Cloud Run hosting (incl. MCP-on-Cloud-Run) | **[cloud/gcp.md](integrations/cloud/gcp.md)** |

---

## Tool registry (single source of truth)

| Tool | Purpose |
|---|---|
| `agcl.mas.run`        | Run one turn through the recursive MAS engine |
| `agcl.mas.status`     | Inspect training-control state of a session |
| `agcl.mas.pause`      | Pause training mid-run (resumable) |
| `agcl.mas.resume`     | Resume a paused session |
| `agcl.mas.halt`       | Halt training cleanly |
| `agcl.mas.latent`     | Snapshot the latest loop latent (truncated) |
| `agcl.mini.test`      | Generate from the background mini-model |
| `agcl.mini.status`    | Mini-trainer status |
| `agcl.mini.start` / `stop` | Toggle the background trainer |
| `agcl.topics.list`    | List saved trained-topic checkpoints |
| `agcl.plugins.list`   | List discovered AGCL plugins |
| `agcl.toolkit.discover` | Snapshot of which infra adapters are configured + importable |
| `agcl.toolkit.ping`   | Reachability check against one toolkit adapter |
| `agcl.toolkit.chat`   | Chat through whichever inference gateway is configured |
| `agcl.toolkit.emit_docker` | Generate Dockerfile + Compose for the current config |
| `agcl.toolkit.emit_k8s` | Generate Helm chart + standalone manifests |
| `agcl.toolkit.emit_gcp` | Generate Cloud Run service.yaml + Cloud Build pipeline |
| `agcl.multica.run`    | (plugin) Run a task in the Multica jsonl event taxonomy |

Plugins extend the registry at runtime via
`agcl.integrations.manifest.register_tool(name, description, schema, handler)`
or, from a plugin's `register(ctx)` callback, via
`ctx.add_tool(name, description, schema, handler)`.

---

## Decision matrix (mapping the guide to AGCL)

| Platform            | AGCL adapter                         | One-liner |
|---|---|---|
| Claude Desktop      | MCP stdio                            | `python main.py mcp` |
| Claude Code         | MCP stdio                            | same |
| Cursor              | MCP stdio                            | same |
| OpenAI Agents SDK   | MCP stdio (or HTTP SSE)              | `python main.py mcp [--transport sse]` |
| LangChain/LangGraph | MCP via `langchain-mcp-adapters`     | same |
| CrewAI              | MCP via `crewai-tools.MCPTool`       | same |
| AutoGen             | MCP via `autogen-ext[mcp]`           | same |
| Copilot Studio      | MCP HTTP SSE *or* OpenAPI            | `python main.py mcp --transport sse` |
| Cloudflare Agents   | MCP                                  | same |
| Slack AI Apps       | Bolt SDK adapter                     | `python main.py slack` |
| Discord             | discord.py adapter                   | `python main.py toolkit discord` |
| Multica.ai          | jsonl runner + plugin                | `python main.py run --output jsonl` |
| OpenAgents          | AgentMod adapter                     | `python main.py agentmod` |
| Zapier              | OpenAPI                              | `python main.py openapi --out agcl.openapi.json` |
| n8n (webhook)       | OpenAPI                              | same |
| Vertex AI Ext.      | OpenAPI                              | same |
| Vercel AI SDK       | MCP                                  | `python main.py mcp` |

---

## Adding a new tool to all platforms at once

```python
# in any plugin under plugins/
from agcl.integrations.manifest import register_tool

def my_handler(args):
    return {"echo": args.get("text", "")}

def register(ctx):
    register_tool(
        name="my.echo",
        description="Echo input text back",
        schema={"type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"]},
        handler=my_handler,
    )
```

Restart any adapter and `my.echo` shows up in MCP, OpenAgents,
Slack-direct-call (via the manifest dump), and the OpenAPI spec.
