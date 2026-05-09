"""CLI entry point for AGCL (scaffold)."""

from __future__ import annotations

import argparse


def main() -> None:
    raise SystemExit(_main())


def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="agcl",
        description="AGCL hybrid control-plane CLI (scaffold).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the package version and exit",
    )
    args = parser.parse_args()
    if args.version:
        from agcl import __version__

        print(__version__)
        return 0
    parser.print_help()
    return 0
