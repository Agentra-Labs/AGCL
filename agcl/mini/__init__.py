"""
AGCL mini-model background trainer.

A small, optional, pluggable model that trains itself off the latents
and reasoning trajectories produced during normal AGCL use. Toggle it
on with `MINI_ENABLED=1` (or via the TUI menu / `/mini start`); it
spins up a low-priority background thread that consumes a queue of
samples emitted by the main engine and steps a tiny optimizer between
yields. Pause / resume / test are all hot-callable from the CLI, the
TUI, and the `/node/mini/*` HTTP routes.

Public surface:
    runtime.get_trainer()        -> the singleton MiniTrainer
    runtime.append_sample(...)   -> called by RecursiveSession after a turn
    MiniConfig, MiniModel, MiniMLP, MiniTrainer
    PRESETS                      -> training strategy registry
    ATTENTION_MASKS              -> attention preset registry
"""

from .config       import MiniConfig
from .attention    import ATTENTION_MASKS, build_attention_mask
from .model        import MiniModel, MiniMLP, build_arch
from .strategies   import PRESETS, compose_loss
from .trainer      import MiniTrainer
from . import runtime

__all__ = [
    "MiniConfig", "MiniModel", "MiniMLP", "build_arch",
    "ATTENTION_MASKS", "build_attention_mask",
    "PRESETS", "compose_loss",
    "MiniTrainer", "runtime",
]
