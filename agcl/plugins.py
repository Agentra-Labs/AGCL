"""
AGCL plugin loader.

A plugin is a single Python file dropped in the project's `plugins/`
directory (configurable via the `AGCL_PLUGINS_DIR` env var). Each plugin
exposes a top-level `register(ctx)` function that AGCL calls once at
startup. Through `ctx`, the plugin can:

  - register HTTP routes on the node app    (ctx.add_route / ctx.router)
  - register CLI commands for the TUI       (ctx.add_command)
  - reach the live MAS singleton + sessions (ctx.get_mas / ctx.sessions)
  - read / mutate the per-session control   (ctx.get_control)

Plugins are *not* sandboxed — they run with the same privileges as the
host process. Only load plugins you trust.

Failures in one plugin must not stop the rest from loading. Each plugin
is imported with a try/except that records the error and surfaces it via
`/node/plugins`.

Example plugin (plugins/hello.py):

    def register(ctx):
        @ctx.router.get("/hello")
        def hello():
            return {"hello": "world"}

        @ctx.add_command("hello", help="say hi")
        def _cli_hello(args):
            print("hi from plugin")

The contract is documented for users in docs/plugins.md.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# Module-level state. Populated by load_plugins() and read by the
# `/node/plugins` route + the TUI command palette.
_LOADED: List[Dict[str, Any]] = []
_COMMANDS: Dict[str, Dict[str, Any]] = {}
_LIFECYCLE: Dict[str, List[Callable]] = {
    "startup": [], "shutdown": [], "before_turn": [], "after_turn": [],
}
_CONFIG_DECLARATIONS: List[Dict[str, Any]] = []
_PLUGIN_TOOLS: List[Dict[str, Any]] = []


def plugins_dir() -> Path:
    return Path(os.environ.get("AGCL_PLUGINS_DIR", "plugins")).resolve()


class PluginContext:
    """Handle passed to each plugin's register() function."""

    def __init__(self, router: Any) -> None:
        self.router = router  # the FastAPI APIRouter
        self._config_keys: List[Dict[str, Any]] = []
        self._tools: List[Dict[str, Any]] = []
        self._lifecycle: Dict[str, List[Callable]] = {
            "startup": [], "shutdown": [], "before_turn": [], "after_turn": [],
        }

    # --- HTTP ---

    def add_route(self, path: str, handler: Callable, *,
                  method: str = "GET", **kw: Any) -> None:
        """Imperative route registration (alternative to @router decorators)."""
        method_lower = method.lower()
        register = getattr(self.router, method_lower, None)
        if register is None:
            raise ValueError(f"unsupported method {method!r}")
        register(path, **kw)(handler)

    # --- CLI ---

    def add_command(self, name: str, help: str = "",
                    aliases: Optional[List[str]] = None) -> Callable:
        """Decorator: registers a CLI command callable as `fn(args: str)`."""
        def deco(fn: Callable[[str], Any]) -> Callable[[str], Any]:
            _COMMANDS[name] = {
                "name": name,
                "help": help,
                "aliases": list(aliases or []),
                "fn": fn,
            }
            for a in aliases or []:
                _COMMANDS[a] = _COMMANDS[name]
            return fn
        return deco

    # --- Tool surface (auto-registers in MCP/Slack/OpenAgents) ---

    def add_tool(self, name: str, description: str,
                  schema: Dict[str, Any],
                  handler: Callable[[Dict[str, Any]], Any]) -> None:
        """
        Plugin-registered tool: appears in every adapter (MCP, Slack,
        OpenAgents, OpenAPI dump) without core code changes.
        """
        from agcl.integrations.manifest import register_tool
        register_tool(name=name, description=description,
                       schema=schema, handler=handler)
        self._tools.append({"name": name, "description": description})

    # --- Config metadata (declare what env vars / files this plugin needs) ---

    def declare_config(self, *,
                        env_vars: Optional[List[Dict[str, Any]]] = None,
                        files:    Optional[List[str]] = None,
                        extras:   Optional[List[str]] = None) -> None:
        """
        Declare the plugin's config surface so AGCL's config-bundle
        validator can hint about it. `env_vars` items look like:
            {"name": "FOO_TOKEN", "required": True,
             "secret": True, "doc": "API key for FOO"}
        """
        self._config_keys.append({
            "env_vars": env_vars or [],
            "files":    files or [],
            "extras":   extras or [],
        })

    # --- Lifecycle hooks ---

    def on_startup(self, fn: Callable) -> Callable:
        self._lifecycle["startup"].append(fn);  return fn

    def on_shutdown(self, fn: Callable) -> Callable:
        self._lifecycle["shutdown"].append(fn);  return fn

    def on_before_turn(self, fn: Callable) -> Callable:
        self._lifecycle["before_turn"].append(fn);  return fn

    def on_after_turn(self, fn: Callable) -> Callable:
        self._lifecycle["after_turn"].append(fn);  return fn

    # --- MAS introspection ---

    def get_mas(self) -> Any:
        """Return the live MAS singleton (building it if needed)."""
        from agcl.node import _build_mas_singleton
        return _build_mas_singleton()

    def sessions(self) -> Dict[str, Any]:
        from agcl.node import _mas_sessions
        return _mas_sessions

    def get_control(self, session_id: str):
        sess = self.sessions().get(session_id)
        return sess.control if sess is not None else None


def load_plugins(router: Any) -> List[Dict[str, Any]]:
    """
    Discover every `*.py` file in plugins_dir() and call its register().
    Returns the list of {name, ok, error?} entries kept in module state.
    """
    _LOADED.clear()
    _CONFIG_DECLARATIONS.clear()
    _PLUGIN_TOOLS.clear()
    for k in _LIFECYCLE: _LIFECYCLE[k].clear()
    d = plugins_dir()
    if not d.is_dir():
        return _LOADED

    for path in sorted(d.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = path.stem
        ctx = PluginContext(router)
        entry: Dict[str, Any] = {"name": name, "path": str(path), "ok": False}
        try:
            spec = importlib.util.spec_from_file_location(
                f"agcl_plugin_{name}", path,
            )
            if spec is None or spec.loader is None:
                raise ImportError("spec_from_file_location returned None")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            register = getattr(mod, "register", None)
            if register is None:
                raise AttributeError("plugin has no register(ctx) function")
            register(ctx)
            entry["ok"] = True
            entry["doc"] = (mod.__doc__ or "").strip().splitlines()[0:1]
            entry["tools"] = [t["name"] for t in ctx._tools]
            entry["config"] = ctx._config_keys
            # Aggregate this plugin's lifecycle hooks into the global map
            # so callers (node startup, recursive session) can fire them.
            for k, fns in ctx._lifecycle.items():
                _LIFECYCLE[k].extend(fns)
            if ctx._config_keys:
                _CONFIG_DECLARATIONS.append(
                    {"plugin": name, "config": ctx._config_keys},
                )
            if ctx._tools:
                _PLUGIN_TOOLS.append({"plugin": name, "tools": ctx._tools})
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            entry["traceback"] = traceback.format_exc()
        _LOADED.append(entry)
    return _LOADED


def fire_lifecycle(stage: str, *args, **kwargs) -> List[Any]:
    """Invoke every registered lifecycle hook for a given stage."""
    out = []
    for fn in _LIFECYCLE.get(stage, []):
        try:
            out.append(fn(*args, **kwargs))
        except Exception as e:
            out.append({"plugin_hook_error": f"{type(e).__name__}: {e}"})
    return out


def declared_configs() -> List[Dict[str, Any]]:
    return list(_CONFIG_DECLARATIONS)


def plugin_tools() -> List[Dict[str, Any]]:
    return list(_PLUGIN_TOOLS)


def loaded() -> List[Dict[str, Any]]:
    """Snapshot of plugin load results (for the /node/plugins route)."""
    return list(_LOADED)


def commands() -> Dict[str, Dict[str, Any]]:
    """Return the {name: spec} map of CLI commands plugins registered."""
    return dict(_COMMANDS)


def run_command(name: str, args: str = "") -> Any:
    """Look up and invoke a registered CLI command."""
    spec = _COMMANDS.get(name)
    if spec is None:
        raise KeyError(f"no such plugin command: {name!r}")
    return spec["fn"](args)
