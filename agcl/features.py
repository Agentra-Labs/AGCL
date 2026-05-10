"""
Central feature registry.

One source of truth for "what can AGCL do?" — used by:

    - the TUI shell to populate its main menu
    - `python main.py --help` and the help screen
    - the docs / readme component matrix
    - plugins that want to surface alongside builtins

A Feature is a small dataclass:

    name        short id (matches the CLI subcommand when applicable)
    label       one-line description shown in menus
    group       "core" | "agent" | "infra" | "ops" | "ext"
    cli         the CLI command equivalent ("python main.py …") or None
    handler     callable(session, linter) -> None  for in-process TUI launch
    docs        relative path to the user-facing doc

Plugins can extend the registry at runtime via `register_feature(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Feature:
    name:    str
    label:   str
    group:   str = "core"
    cli:     Optional[str] = None
    handler: Optional[Callable[..., Any]] = None
    docs:    Optional[str] = None
    requires_models: bool = False     # warn user if no MAS / GGUF configured

    def menu_row(self) -> str:
        return f"{self.name} - {self.label}"


_FEATURES: List[Feature] = []


def register_feature(f: Feature) -> Feature:
    _FEATURES.append(f)
    return f


def all_features() -> List[Feature]:
    return list(_FEATURES)


def by_group(group: str) -> List[Feature]:
    return [f for f in _FEATURES if f.group == group]


def find(name: str) -> Optional[Feature]:
    for f in _FEATURES:
        if f.name == name:
            return f
    return None


# ----------------------------------------------------------------------
# Builtin registration. Handlers are filled in below as forward refs.
# Defined here so any import of agcl.features sees the full list, then
# the TUI binds its handler functions in agcl.tui.
# ----------------------------------------------------------------------

def _bootstrap() -> None:
    if _FEATURES:
        return
    register_feature(Feature(
        name="chat",      label="prefix + cloud passthrough",
        group="agent",
        cli="python main.py chat",
        docs="docs/guide.md",
    ))
    register_feature(Feature(
        name="recursive", label="recursive multi-agent reasoning (RecursiveMAS)",
        group="agent",
        cli="python main.py recursive run",
        docs="docs/recursive.md",
        requires_models=True,
    ))
    register_feature(Feature(
        name="run",       label="headless task runner (jsonl/sse/text output)",
        group="agent",
        cli="python main.py run --task '<msg>' --output jsonl",
        docs="docs/integrations/headless-run.md",
    ))
    register_feature(Feature(
        name="orchestrate", label="drive remote AGCL nodes via playbook or one-shot",
        group="agent",
        cli="python main.py orchestrate --target <url> --task '<msg>'",
        docs="docs/integrations/headless-run.md",
    ))
    register_feature(Feature(
        name="minimodel", label="background mini-model trainer (toggleable)",
        group="agent",
        cli="python main.py mini status",
        docs="docs/minimodel.md",
    ))
    register_feature(Feature(
        name="topics",    label="list saved trained-state checkpoints",
        group="agent",
        cli="python main.py recursive info",
        docs="docs/recursive/setup.md",
    ))
    register_feature(Feature(
        name="toolkit",   label="infra adapters (litellm, ollama, vllm, redis, s3, gcp)",
        group="infra",
        cli="python main.py toolkit discover",
        docs="docs/integrations/cloud.md",
    ))
    register_feature(Feature(
        name="server",    label="open this PC to a GUI client (HTTP+SSE node)",
        group="infra",
        cli="python main.py node",
        docs="docs/gui.md",
    ))
    register_feature(Feature(
        name="dashboard", label="open the token / quota / API dashboard in browser",
        group="infra",
        cli="open http://localhost:9876/node/dashboard",
        docs="docs/dashboard.md",
    ))
    register_feature(Feature(
        name="usage",     label="text-mode token + cost summary",
        group="infra",
        cli="curl /node/usage/summary",
        docs="docs/dashboard.md",
    ))
    register_feature(Feature(
        name="api",       label="API management — quotas + custom OpenAI-compatible providers",
        group="infra",
        cli="(in-shell only)",
        docs="docs/dashboard.md",
    ))
    register_feature(Feature(
        name="sessions",  label="manage live MAS sessions (halt / pause / resume / latent)",
        group="agent",
        cli="curl /node/mas/sessions",
        docs="docs/gui/endpoints.md",
    ))
    register_feature(Feature(
        name="diagnostics", label="health check: validate + ping all + tool inventory",
        group="ops",
        cli="python main.py recursive validate",
        docs="docs/recursive/setup.md",
    ))
    register_feature(Feature(
        name="secrets",   label=".env editor with secret masking",
        group="ops",
        cli="(in-shell only)",
        docs="docs/configuration.md",
    ))
    register_feature(Feature(
        name="bundle",    label="config export / import / hint (share setups)",
        group="ops",
        cli="python main.py config export agcl-config.json",
        docs="docs/configuration.md",
    ))
    register_feature(Feature(
        name="config",    label="interactive RecursiveMAS configurator (wizard)",
        group="ops",
        cli="python main.py --config",
        docs="docs/configuration.md",
    ))
    register_feature(Feature(
        name="autoconfig", label="one-shot canonical 2-agent HF setup",
        group="ops",
        cli="python main.py autoconfig",
        docs="docs/recursive/advanced.md",
    ))
    register_feature(Feature(
        name="plugins",   label="list / reload discovered plugins",
        group="ext",
        cli="curl /node/plugins",
        docs="docs/plugins.md",
    ))
    register_feature(Feature(
        name="mcp",       label="run an MCP server (stdio | sse | manifest)",
        group="ext",
        cli="python main.py mcp",
        docs="docs/integrations/mcp.md",
    ))
    register_feature(Feature(
        name="slack",     label="run the Slack Bolt adapter",
        group="ext",
        cli="python main.py slack",
        docs="docs/integrations/slack.md",
    ))
    register_feature(Feature(
        name="discord",   label="run the Discord bot adapter",
        group="ext",
        cli="python main.py toolkit discord",
        docs="docs/integrations/discord.md",
    ))
    register_feature(Feature(
        name="agentmod",  label="run the OpenAgents AgentMod",
        group="ext",
        cli="python main.py agentmod",
        docs="docs/integrations/openagents.md",
    ))
    register_feature(Feature(
        name="openapi",   label="dump the OpenAPI 3.0 spec",
        group="ext",
        cli="python main.py openapi --out agcl.openapi.json",
        docs="docs/integrations/openapi.md",
    ))
    register_feature(Feature(
        name="help",      label="full slash-command + feature reference",
        group="core",
    ))
    register_feature(Feature(
        name="quit",      label="exit",
        group="core",
    ))


_bootstrap()


# ----------------------------------------------------------------------
# Slash-command catalog (used by the TUI linter + help screen)
# ----------------------------------------------------------------------

SLASH_COMMANDS: Dict[str, str] = {
    # navigation
    "back":     "return to the main menu",
    "quit":     "exit the shell",
    "help":     "show full slash-command reference",

    # recursive session control
    "topics":   "list saved trained topics",
    "rebuild":  "drop loaded MAS singleton; next turn rebuilds",
    "halt":     "halt training in current session (mid-step)",
    "pause":    "pause training (resumable)",
    "resume":   "resume after a /pause",
    "status":   "show training-control state",
    "latent":   "dump latest latent snapshot (truncated)",
    "cloud":    "force this turn through cloud only",
    "continue": "force local-prefix + cloud-finish for this turn",
    "strict":   "toggle strict mode (anti-hallucination) for this session",

    # mini-trainer
    "mini":         "show mini-trainer status",
    "mini-start":   "enable + start the mini-trainer thread",
    "mini-stop":    "stop the mini-trainer thread",
    "mini-pause":   "pause the mini-trainer",
    "mini-resume":  "resume the mini-trainer",
    "mini-status":  "show mini-trainer status",
    "mini-test":    "generate a sample from the mini-model",
    "mini-config":  "show mini-trainer config",
    "mini-presets": "list strategies / attention masks / archs",

    # operations
    "server":   "start the node HTTP+SSE server in this process",
    "config":   "open the RecursiveMAS configurator wizard",
    "export":   "export config bundle (env + mas.json) to file",
    "import":   "import a config bundle (with dependency hints)",
    "hint":     "validate a config bundle without applying",

    # toolkit
    "discover": "show which toolkit adapters are configured",
    "ping":     "reachability check on a toolkit adapter",
    "emit":     "emit Dockerfile / Helm chart / Cloud Run manifest",

    # plugin / api management surfaces (sticky inside the relevant submode)
    "reload":      "rediscover plugins (from any mode)",
    "list":        "list (in api mode = providers; in sessions = sessions; etc.)",
    "view":        "view detail of a session by id",
    "quota":       "set a quota: /quota <provider> tokens=N | usd=N period=daily",
    "clear-quota": "clear a provider's quota",
    "custom":      "/custom add|rm|list — custom OpenAI-compatible providers",
    "set":         "(secrets mode) /set <KEY> <value>  — writes .env",
    "unset":       "(secrets mode) /unset <KEY>",
    "test":        "(secrets mode) /test <KEY> — print masked value of an env var",
    "target":      "(orchestrate mode) /target <url> — set the remote node",
    "key":         "(orchestrate mode) /key <bearer> — set the auth key",
}


def slash_command_names() -> List[str]:
    return list(SLASH_COMMANDS.keys())
