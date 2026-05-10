"""
agcl — Agentic CLI runtime + recursive cognition layer + orchestrator.

The top-level `agcl` package houses the real implementation
(`agcl.recursive`, `agcl.toolkit`, `agcl.tui`, `agcl.usage`,
`agcl.dashboard`, etc.). The `src/agcl` directory is a thin scaffold
used by the setuptools console-script entry point — it delegates to
this package at runtime.

Keep `__version__` here aligned with the version declared in
`pyproject.toml`; `tests/test_package.py` asserts they match.
"""

__version__ = "0.1.0"
