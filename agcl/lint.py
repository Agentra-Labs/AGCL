"""
CLI input linting for the AGCL TUI.

Two pieces:

  - `lint(line, commands)` validates a single line against the known
    command set. Returns a `LintResult` with severity, message, and
    optional suggestion (Levenshtein nearest match).

  - `Linter` keeps a registry that the TUI updates as plugins load, and
    is callable for one-shot checks.

Goals:
  - friendly: surface typos with "did you mean ...?" instead of silent
    failure
  - cheap: pure stdlib, runs on every keystroke if you want
  - extensible: plugins add commands at runtime via Linter.register()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class LintResult:
    ok: bool
    message: str = ""
    suggestion: Optional[str] = None
    severity: str = "info"   # "info" | "warn" | "error"

    def render(self) -> str:
        if self.ok:
            return ""
        bits = [f"[{self.severity}] {self.message}"]
        if self.suggestion:
            bits.append(f"did you mean: {self.suggestion} ?")
        return "  ".join(bits)

    def hint(self) -> str:
        """Claude-Code-style inline hint: one dim line, no severity tag.
        Returns "" when there's nothing useful to say so callers can use
        `if hint: print(hint)` without checking ok separately."""
        if self.ok:
            return ""
        if self.suggestion:
            return f"did you mean {self.suggestion}?"
        return self.message


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                curr[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def nearest(target: str, candidates: Iterable[str],
            max_dist: int = 3) -> Optional[str]:
    """Return the closest string in candidates within max_dist, else None."""
    best: Optional[str] = None
    best_d = max_dist + 1
    for c in candidates:
        d = _levenshtein(target, c)
        if d < best_d:
            best, best_d = c, d
    return best


def lint(line: str, commands: List[str]) -> LintResult:
    s = line.strip()
    if not s:
        return LintResult(ok=True)
    if not s.startswith("/"):
        # Not a directive — TUI treats it as input to the active mode.
        return LintResult(ok=True)
    head = s.split(None, 1)[0]
    name = head[1:]
    if name in commands:
        return LintResult(ok=True)
    suggestion = nearest(name, commands)
    return LintResult(
        ok=False,
        message=f"unknown command: /{name}",
        suggestion=f"/{suggestion}" if suggestion else None,
        severity="warn",
    )


class Linter:
    def __init__(self, commands: Optional[List[str]] = None) -> None:
        self._cmds: List[str] = list(commands or [])

    def register(self, *names: str) -> None:
        for n in names:
            if n not in self._cmds:
                self._cmds.append(n)

    def unregister(self, name: str) -> None:
        try:
            self._cmds.remove(name)
        except ValueError:
            pass

    def commands(self) -> List[str]:
        return list(self._cmds)

    def __call__(self, line: str) -> LintResult:
        return lint(line, self._cmds)
