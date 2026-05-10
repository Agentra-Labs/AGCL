"""
External-tool detection.

AGCL doesn't insist that everything live inside the Python process.
When `gh`, `docker`, `kubectl`, `gcloud`, `curl`, `jq`, etc. are
installed, submodes can shell out to them for tasks where the system
binary is the right tool — `gh pr create`, `docker compose up`,
`kubectl apply`, etc.

This module is the single discovery surface:

    have("docker")          -> bool
    path("gh")              -> Optional[str]
    version("kubectl")      -> Optional[str]   (stdout of `kubectl version --client`)
    detect_all()            -> Dict[str, dict] (one entry per known tool)

The TUI uses this to render an "available tools" panel and to gate
menu items: e.g. the dashboard mode only offers "open in browser"
when a usable opener is on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Dict, Optional


# Tools we know how to talk to. Each entry is (binary, version_args).
KNOWN: Dict[str, tuple] = {
    "git":     ("git",     ("--version",)),
    "gh":      ("gh",      ("--version",)),
    "docker":  ("docker",  ("--version",)),
    "compose": ("docker",  ("compose", "version")),
    "podman":  ("podman",  ("--version",)),
    "kubectl": ("kubectl", ("version", "--client", "--output=yaml")),
    "helm":    ("helm",    ("version", "--short")),
    "gcloud":  ("gcloud",  ("--version",)),
    "aws":     ("aws",     ("--version",)),
    "curl":    ("curl",    ("--version",)),
    "jq":      ("jq",      ("--version",)),
    "uv":      ("uv",      ("--version",)),
    "node":    ("node",    ("--version",)),
    "bun":     ("bun",     ("--version",)),
    "npm":     ("npm",     ("--version",)),
    "ollama":  ("ollama",  ("--version",)),
}


def have(tool: str) -> bool:
    bin_, _ = KNOWN.get(tool, (tool, ()))
    return shutil.which(bin_) is not None


def path(tool: str) -> Optional[str]:
    bin_, _ = KNOWN.get(tool, (tool, ()))
    return shutil.which(bin_)


def version(tool: str, timeout: float = 2.0) -> Optional[str]:
    spec = KNOWN.get(tool)
    if spec is None:
        return None
    bin_, args = spec
    p = shutil.which(bin_)
    if p is None:
        return None
    try:
        out = subprocess.check_output(
            [p, *args], stderr=subprocess.STDOUT, timeout=timeout,
        ).decode(errors="replace").strip()
        # Most tools print their version on the first line.
        return out.splitlines()[0] if out else ""
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None


def detect_all() -> Dict[str, Dict[str, Optional[str]]]:
    """Inventory every known tool. Returns:
        {tool: {"path": "...", "version": "...", "ok": bool}}
    """
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for name in KNOWN:
        p = path(name)
        out[name] = {
            "path":    p,
            "version": version(name) if p else None,
            "ok":      p is not None,
        }
    return out


def best_browser_opener() -> Optional[str]:
    """Return the binary that should open a URL in the user's browser.
    Picks the first available of: xdg-open (Linux), open (macOS),
    explorer (Windows wsl). None means no opener — print the URL."""
    for cand in ("xdg-open", "open", "explorer.exe"):
        if shutil.which(cand):
            return cand
    return None
