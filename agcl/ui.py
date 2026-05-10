"""
Terminal UI primitives.

One module that owns:
  - color palette  (truecolor where supported, 16-color fallback,
                     auto-disable when stdout isn't a TTY or NO_COLOR is set)
  - timeline-style activity blocks (▶ command, ↳ step, ✓/✗ result)
  - spinner + elapsed-timer for long ops
  - inline diff + key:value rendering

Colors follow the developer-tool conventions described by the user:

    Background    → terminal default
    Primary text  → terminal default
    User prompt   → cool white / cyan-tinted   (cyan)
    Reasoning     → neutral gray               (dim)
    Commands      → amber                      (yellow)
    Success       → green
    Errors        → red
    Info / meta   → dim blue-gray              (blue, dim)
    Active op     → cyan accent
    Diff +        → green
    Diff -        → red

The ANSI sequences are emitted directly so this module has zero deps;
prompt_toolkit (used elsewhere) just sees a string and paints it as-is.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import threading
from contextlib import contextmanager
from typing import Iterable, Iterator, Optional


# ----------------------------------------------------------------------
# Capability detection
# ----------------------------------------------------------------------

def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):           # https://no-color.org/
        return False
    if os.environ.get("AGCL_NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return _is_tty()


def _supports_truecolor() -> bool:
    if not _supports_color():
        return False
    ct = (os.environ.get("COLORTERM") or "").lower()
    return ct in ("truecolor", "24bit")


COLOR    = _supports_color()
TRUECOLOR = _supports_truecolor()


# ----------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------

# 16-color codes, used as the floor when truecolor isn't available.
_BASE = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "italic":  "\033[3m",
    "under":   "\033[4m",

    # foregrounds (8-color)
    "black":   "\033[30m",
    "red":     "\033[31m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "cyan":    "\033[36m",
    "white":   "\033[37m",
    "gray":    "\033[90m",

    # bright variants
    "bred":     "\033[91m",
    "bgreen":   "\033[92m",
    "byellow":  "\033[93m",
    "bblue":    "\033[94m",
    "bmagenta": "\033[95m",
    "bcyan":    "\033[96m",
    "bwhite":   "\033[97m",
}


def _truecolor_fg(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


# Semantic palette — what other modules import.
class Palette:
    """Semantic colors. Look these up, not raw escapes."""
    @staticmethod
    def _c(name: str) -> str:
        return _BASE[name] if COLOR else ""

    @classmethod
    def reset(cls):    return cls._c("reset")
    @classmethod
    def bold(cls):     return cls._c("bold")
    @classmethod
    def dim(cls):      return cls._c("dim")

    @classmethod
    def prompt(cls):   return cls._c("bcyan")    # user input cursor / labels
    @classmethod
    def reasoning(cls): return cls._c("dim")    # planning / thinking lines
    @classmethod
    def command(cls):  return cls._c("byellow") # shell commands, user actions
    @classmethod
    def success(cls):  return cls._c("bgreen")  # ✓ done states
    @classmethod
    def error(cls):    return cls._c("bred")    # ✗ stderr / exceptions
    @classmethod
    def info(cls):     return (cls._c("dim") + cls._c("blue")) if COLOR else ""
    @classmethod
    def active(cls):   return cls._c("cyan")    # in-progress operations
    @classmethod
    def diff_add(cls): return cls._c("green")
    @classmethod
    def diff_del(cls): return cls._c("red")
    @classmethod
    def accent(cls):
        # gradient-friendly accent — cyan-leaning truecolor on capable terms
        if TRUECOLOR:
            return _truecolor_fg(135, 206, 250)   # light sky blue
        return cls._c("bcyan")


# ----------------------------------------------------------------------
# Top-level paint helpers (most callers will use these)
# ----------------------------------------------------------------------

def paint(text: str, color: str) -> str:
    """Wrap text in `color` + reset. `color` is one of the Palette methods."""
    if not COLOR:
        return text
    return f"{color}{text}{Palette.reset()}"


def styled(text: str, *, fg: Optional[str] = None,
            bold: bool = False, dim: bool = False, italic: bool = False) -> str:
    """Compose multiple styles into one wrap."""
    if not COLOR:
        return text
    pre = ""
    if bold:   pre += _BASE["bold"]
    if dim:    pre += _BASE["dim"]
    if italic: pre += _BASE["italic"]
    if fg:     pre += fg
    if not pre:
        return text
    return f"{pre}{text}{Palette.reset()}"


# Convenience shortcuts
def cmd(text: str) -> str:    return paint(text, Palette.command())
def ok(text: str) -> str:     return paint(text, Palette.success())
def err(text: str) -> str:    return paint(text, Palette.error())
def info(text: str) -> str:   return paint(text, Palette.info())
def hint(text: str) -> str:   return paint(text, Palette.reasoning())
def active(text: str) -> str: return paint(text, Palette.active())
def accent(text: str) -> str: return paint(text, Palette.accent())


# ----------------------------------------------------------------------
# Timeline blocks
# ----------------------------------------------------------------------

ICON_ARROW   = "▶"
ICON_BRANCH  = "↳"
ICON_OK      = "✓"
ICON_FAIL    = "✗"
ICON_BULLET  = "•"


def header(text: str) -> None:
    """Underlined section header."""
    sys.stdout.write(styled(f"\n{text}\n", bold=True) + "\n")


def step_run(label: str) -> None:
    """`▶ label` in command color — the start of a tool/op."""
    sys.stdout.write(f"{paint(ICON_ARROW, Palette.command())} "
                      f"{paint(label, Palette.command())}\n")


def step_branch(label: str) -> None:
    """`  ↳ label` indented under the most recent step."""
    sys.stdout.write(f"  {paint(ICON_BRANCH, Palette.active())} "
                      f"{paint(label, Palette.reasoning())}\n")


def step_ok(label: str = "done") -> None:
    sys.stdout.write(f"  {paint(ICON_OK, Palette.success())} "
                      f"{paint(label, Palette.success())}\n")


def step_fail(label: str) -> None:
    sys.stdout.write(f"  {paint(ICON_FAIL, Palette.error())} "
                      f"{paint(label, Palette.error())}\n")


def kv(key: str, value: str, key_w: int = 12) -> None:
    """Aligned key/value line: dim key, normal value."""
    sys.stdout.write(f"  {paint(key.ljust(key_w), Palette.dim())} {value}\n")


# ----------------------------------------------------------------------
# Spinner / elapsed timer
# ----------------------------------------------------------------------

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@contextmanager
def spinner(label: str) -> Iterator[None]:
    """
    `with spinner("loading models"): ...` — animates while the block
    runs, prints `✓ loading models (1.2s)` on exit (or `✗ ...` on error).
    Falls back to a simple `▶ label` line on non-TTYs.
    """
    if not COLOR or not _is_tty():
        step_run(label)
        t0 = time.time()
        try:
            yield
        except Exception:
            step_fail(f"{label} failed")
            raise
        step_ok(f"{label} ({time.time()-t0:.1f}s)")
        return

    stop = threading.Event()
    t0 = time.time()

    def _animate() -> None:
        i = 0
        while not stop.is_set():
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            sys.stdout.write(
                f"\r{paint(frame, Palette.active())} {paint(label, Palette.reasoning())} "
                f"{paint(f'({time.time()-t0:.1f}s)', Palette.dim())}"
            )
            sys.stdout.flush()
            i += 1
            time.sleep(0.08)

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    try:
        yield
    except Exception:
        stop.set(); th.join()
        sys.stdout.write("\r" + " " * (len(label) + 24) + "\r")
        step_fail(f"{label} failed ({time.time()-t0:.1f}s)")
        raise
    stop.set(); th.join()
    sys.stdout.write("\r" + " " * (len(label) + 24) + "\r")
    step_ok(f"{label} ({time.time()-t0:.1f}s)")


# ----------------------------------------------------------------------
# Diff renderer (unified, color-coded)
# ----------------------------------------------------------------------

def render_diff(lines: Iterable[str]) -> None:
    """Print a unified-diff line stream colored Git-style."""
    for ln in lines:
        if ln.startswith("+++") or ln.startswith("---"):
            sys.stdout.write(styled(ln, bold=True, dim=True) + "\n")
        elif ln.startswith("+"):
            sys.stdout.write(paint(ln, Palette.diff_add()) + "\n")
        elif ln.startswith("-"):
            sys.stdout.write(paint(ln, Palette.diff_del()) + "\n")
        elif ln.startswith("@@"):
            sys.stdout.write(paint(ln, Palette.info()) + "\n")
        else:
            sys.stdout.write(styled(ln, dim=True) + "\n")


# ----------------------------------------------------------------------
# Layout helpers
# ----------------------------------------------------------------------

def width() -> int:
    try:
        return shutil.get_terminal_size((80, 24)).columns
    except OSError:
        return 80


def hr(char: str = "─", color: Optional[str] = None) -> None:
    """A horizontal rule the width of the terminal."""
    line = char * max(20, width())
    sys.stdout.write((paint(line, color) if color else line) + "\n")


def banner_box(title: str, subtitle: str = "") -> None:
    """Title + optional subtitle inside a thin rule."""
    hr()
    sys.stdout.write(styled(f" {title}\n", bold=True) + "\n")
    if subtitle:
        sys.stdout.write(styled(f" {subtitle}\n", dim=True) + "\n")
    hr()
