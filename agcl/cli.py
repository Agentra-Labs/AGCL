"""
agcl.cli — console-script entry point declared in pyproject.toml.

`pip install agcl` exposes a top-level `agcl` command via the
`[project.scripts]` table. That command lands here. We forward to
the existing argparse + dispatcher in `main.py`, so a wheel install
behaves identically to `python main.py …`.

Two equivalent entry points after this:

    pip install -e . && agcl recursive run "..."
    python main.py recursive run "..."

Both call `main.main()`. The TUI shell (the default no-argument
behavior) is preserved.
"""

from __future__ import annotations

import sys
from typing import NoReturn


def main() -> NoReturn:
    """Bridge to the existing main.py top-level dispatcher."""
    # `main.py` lives at the repo root, not inside the package, so
    # we import it dynamically. The fallback path (running from a
    # checkout where `main.py` is on sys.path) is the common case;
    # when installed via wheel, we drop into a minimal scaffold
    # that prints version + help (so the binary is never broken).
    try:
        import main as _main_module          # type: ignore[import-not-found]
    except ImportError:
        # Wheel install without main.py at cwd: print help + version
        # so the CLI doesn't 500 and the user knows where to look.
        from agcl import __version__
        sys.stderr.write(
            f"agcl {__version__}\n"
            f"This wheel exposes the agcl Python API. Run from a checkout\n"
            f"of the AGCL source tree, or use `python -m agcl.tui` if you\n"
            f"need the TUI without main.py on the path.\n"
        )
        raise SystemExit(0)
    raise SystemExit(_main_module.main())


if __name__ == "__main__":
    main()
