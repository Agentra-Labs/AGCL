"""
AGCL agent-platform integrations.

Single source of truth (`manifest.TOOLS`) defines every callable AGCL
exposes to external agent platforms. Each adapter (MCP, OpenAgents,
Slack, OpenAPI) wraps that registry instead of redefining tools per
platform. Add a tool once, get all platforms.

Adapters import their third-party SDKs lazily so AGCL itself doesn't
depend on `mcp`, `openagents`, or `slack-bolt`. If you don't use a
given adapter, you don't pay for its dependency.

See docs/integrations.md for the full integration guide.
"""

from .manifest import TOOLS, call_tool, tool_names

__all__ = ["TOOLS", "call_tool", "tool_names"]
