"""
ASCII banner + icon renderer for AGCL.

The icon at icon.png renders to a small grayscale ASCII block using
three characters: `=`, `-`, and space. Pillow does the resize +
grayscale; everything else is stdlib.

Colors come from `agcl.ui`: the icon body uses an accent gradient
(cyan-leaning), the title is bold + accent, the rule lines are dim.
Falls back to plain ASCII when:
  - Pillow isn't installed (-> text logo)
  - the terminal doesn't support color (NO_COLOR / piped stdout)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from agcl import ui as _ui


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


def _color_icon_row(row: str, y: int, height: int) -> str:
    """
    Apply a vertical color gradient to an icon row when truecolor is on.
    Top -> bottom: cool cyan -> light blue -> soft green. Looks alive
    without being loud. Falls back to a single accent color in 16-color.
    """
    if not _ui.COLOR:
        return row
    if not _ui.TRUECOLOR:
        return _ui.paint(row, _ui.Palette.accent())
    # Linear interp between three stops (cyan / sky / mint).
    stops = [(125, 211, 252), (147, 197, 253), (134, 239, 172)]
    t = y / max(1, height - 1)
    if t < 0.5:
        a, b, m = stops[0], stops[1], t * 2
    else:
        a, b, m = stops[1], stops[2], (t - 0.5) * 2
    r = int(a[0] + (b[0] - a[0]) * m)
    g = int(a[1] + (b[1] - a[1]) * m)
    bl = int(a[2] + (b[2] - a[2]) * m)
    return f"\033[38;2;{r};{g};{bl}m{row}\033[0m"


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
        rows.append(_color_icon_row("".join(chars), y, height))
    return "\n".join(rows)


def banner(width: int = 40, *, tagline: str = "Agentic CLI") -> str:
    """
    Composed startup banner: icon (gradient) + title (accent + bold) +
    tagline (dim) + thin rule (dim).

    Set `AGCL_NO_COLOR=1` (or `NO_COLOR=1`) to disable colors and get
    the original mono ASCII back.
    """
    icon = render_icon(width=width)
    bar = _ui.styled("=" * (width + 2), dim=True)
    title = _ui.styled("AGCL · Agentic CLI", bold=True, fg=_ui.Palette.accent())
    sub   = _ui.styled(tagline, dim=True, italic=True)
    title_pad = max(0, (width - 18) // 2)         # 18 = visible chars in title
    sub_pad   = max(0, (width - len(tagline)) // 2)
    return (
        f"{bar}\n"
        f"{icon}\n"
        f"{bar}\n"
        f"{' ' * title_pad}{title}\n"
        f"{' ' * sub_pad}{sub}\n"
        f"{bar}\n"
    )
