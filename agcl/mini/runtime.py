"""
Process-wide MiniTrainer singleton + helpers.

The singleton is created lazily so importing `agcl.mini` doesn't
allocate a model unless the feature is actually used. Hot-reach the
trainer via `get_trainer()`; if `MINI_AUTOSTART=1` (the default) and
`MINI_ENABLED=1`, the first `append_sample()` will also start the
background thread.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import torch

from .config import MiniConfig
from .trainer import MiniTrainer


_LOCK = threading.Lock()
_TRAINER: Optional[MiniTrainer] = None


def get_trainer() -> MiniTrainer:
    global _TRAINER
    with _LOCK:
        if _TRAINER is None:
            _TRAINER = MiniTrainer()
        return _TRAINER


def rebuild() -> MiniTrainer:
    """Tear down the existing trainer + build a new one from current config."""
    global _TRAINER
    with _LOCK:
        if _TRAINER is not None:
            try:
                _TRAINER.stop(timeout=2.0)
            except Exception:
                pass
        _TRAINER = MiniTrainer()
        return _TRAINER


def is_active() -> bool:
    return _TRAINER is not None and _TRAINER.is_running()


def append_sample(*, prompt: str, output: str,
                  latent: Optional[torch.Tensor] = None,
                  reformulations: Optional[List[str]] = None,
                  reasoning: Optional[str] = None) -> None:
    """
    Public hook called by RecursiveSession.turn() after each turn.
    Auto-starts the trainer if config says so and the buffer just got
    its first samples.
    """
    t = get_trainer()
    if not t.config.enabled:
        return
    t.append_sample(
        prompt=prompt, output=output, latent=latent,
        reformulations=reformulations, reasoning=reasoning,
    )
    if t.config.autostart and not t.is_running():
        t.start()


def status() -> Dict[str, Any]:
    if _TRAINER is None:
        return {
            "enabled":  False,
            "running":  False,
            "step":     0,
            "note":     "trainer not yet instantiated",
        }
    return _TRAINER.status()
