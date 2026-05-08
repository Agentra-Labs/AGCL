"""
ASCII banner + icon renderer for AGCL.

The icon at icon.png renders to a small grayscale ASCII block using only
the three characters allowed by the project's CLI style rules: `=`, `-`,
and space. Pillow does the resize + grayscale; everything else is
stdlib. If Pillow is unavailable, fall back to the text logo so the CLI
still launches.

`render_icon(width=40)` returns a multi-line string suitable for
printing at the top of the CLI banner.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


# Three-level brightness ramp. Index 0 is the brightest pixel on a dark
# terminal (light on dark) — adjust if your terminal is light-on-dark vs
# dark-on-light. We invert below so PNG-darkest -> '=' (most ink).
RAMP = [" ", "-", "="]


def _ascii_logo() -> str:
    return (
        "  ===   ====   ===  ==     \n"
        " =   = =    = =   = =      \n"
        " ===== =  === =     =      \n"
        " =   = =    = =   = =      \n"
        " =   =  ==== = ===  =====  \n"
        "       Agentic CLI \n"
    )


def find_icon(start: Optional[Path] = None) -> Optional[Path]:
    """Walk upward from cwd / start dir looking for icon.png."""
    here = Path(start or os.getcwd()).resolve()
    for d in [here, *here.parents]:
        p = d / "icon.png"
        if p.is_file():
            return p
    return None


def render_icon(width: int = 40, height: Optional[int] = None,
                path: Optional[Path] = None) -> str:
    """
    Render icon.png to an ASCII block of `=`, `-`, and space.

    Pillow is imported lazily so the CLI still loads if the user hasn't
    installed it yet — in that case we fall back to the text logo.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return _ascii_logo()

    icon = path or find_icon()
    if icon is None:
        return _ascii_logo()

    try:
        img = Image.open(icon).convert("L")
    except Exception:
        return _ascii_logo()

    src_w, src_h = img.size
    # Terminal cells are ~2x taller than wide; halve target rows.
    if height is None:
        height = max(1, int(width * src_h / src_w / 2))
    img = img.resize((width, height))

    rows: List[str] = []
    for y in range(height):
        chars: List[str] = []
        for x in range(width):
            v = img.getpixel((x, y))  # 0 dark, 255 light
            # PNG-darkest -> '=' (densest), mid -> '-', lightest -> ' '
            inv = 255 - v
            if inv > 170:
                chars.append("=")
            elif inv > 85:
                chars.append("-")
            else:
                chars.append(" ")
        rows.append("".join(chars))
    return "\n".join(rows)


def banner(width: int = 40) -> str:
    """Composed startup banner: icon + title + tagline."""
    icon = render_icon(width=width)
    bar = "=" * (width + 2)
    title = "AGCL - Agentic CLI"
    pad = max(0, (width - len(title)) // 2)
    return (
        f"{bar}\n"
        f"{icon}\n"
        f"{bar}\n"
        f"{' ' * pad}{title}\n"
        f"{bar}\n"
    )
