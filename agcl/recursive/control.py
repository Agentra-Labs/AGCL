"""
Runtime control for the auto-training loop.

A `TrainingControl` is a thread-safe handle that the training loop polls
once per step. External callers (the node /node/mas/halt|pause|resume
endpoints, plugins, the TUI) flip its flags to interrupt training without
corrupting gradient state.

Contract for the training loop:
  - call `wait_if_paused()` at the top of each step
  - call `should_halt()` after that and raise HaltedError if true
  - call `record_latent(latent)` after the forward pass so the latent
    snapshot endpoint has something current to return
  - call `reset()` before each fresh training run

The same handle is exposed on RecursiveSession as `.control`, and on the
node module as a per-MAS singleton accessible by HTTP routes.
"""

from __future__ import annotations

import threading
from typing import Any, Optional


class HaltedError(RuntimeError):
    """Raised inside the training loop when halt() has been signalled."""


class TrainingControl:
    def __init__(self) -> None:
        self._paused = threading.Event()
        self._halted = threading.Event()
        self._lock = threading.Lock()
        self._latent: Optional[list] = None
        self._latent_shape: Optional[list] = None
        self._step: int = 0
        self._stage: str = "idle"

    # --- mutators called from outside the loop -----------------------

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def halt(self) -> None:
        self._halted.set()
        self._paused.clear()  # un-block any pause loop so HaltedError fires

    def reset(self) -> None:
        self._paused.clear()
        self._halted.clear()
        with self._lock:
            self._latent = None
            self._latent_shape = None
            self._step = 0
            self._stage = "idle"

    # --- inspectors --------------------------------------------------

    def is_paused(self) -> bool:
        return self._paused.is_set()

    def is_halted(self) -> bool:
        return self._halted.is_set()

    def status(self) -> dict:
        return {
            "paused":  self._paused.is_set(),
            "halted":  self._halted.is_set(),
            "stage":   self._stage,
            "step":    self._step,
            "latent_shape": self._latent_shape,
            "has_latent":  self._latent is not None,
        }

    def latent_snapshot(self) -> Optional[dict]:
        """Return a JSON-safe snapshot of the most recent latent, or None."""
        with self._lock:
            if self._latent is None:
                return None
            return {
                "shape":  self._latent_shape,
                "values": self._latent,
                "stage":  self._stage,
                "step":   self._step,
            }

    # --- hooks called from inside the loop ---------------------------

    def should_halt(self) -> bool:
        return self._halted.is_set()

    def wait_if_paused(self, poll_sec: float = 0.1) -> None:
        """Block until resumed or halted. Cooperatively interruptible."""
        while self._paused.is_set() and not self._halted.is_set():
            self._halted.wait(poll_sec)

    def record_latent(self, latent: Any) -> None:
        """Snapshot the current latent for the inspector endpoint."""
        try:
            t = latent.detach()
            shape = list(t.shape)
            flat = t.flatten().cpu().tolist()
            # Cap stored values so a giant tensor can't bloat memory.
            if len(flat) > 4096:
                flat = flat[:4096]
        except Exception:
            return
        with self._lock:
            self._latent = flat
            self._latent_shape = shape

    def mark(self, stage: str, step: int) -> None:
        with self._lock:
            self._stage = stage
            self._step = step
