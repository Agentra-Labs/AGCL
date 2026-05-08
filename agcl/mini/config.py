"""
MiniConfig - the dataclass that drives the mini trainer.

Defaults pull from `agcl.config` env vars; everything is overridable on
construction or via PATCH /node/mini/config at runtime. Changes that
require rebuilding the model (arch, hidden, layers, vocab, max_seq)
return `requires_rebuild=True` so callers know to call
runtime.rebuild() before training again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

from agcl import config as cfg


REBUILD_FIELDS = {
    "arch", "hidden", "layers", "heads", "max_seq",
    "vocab_size", "attention", "window",
}


@dataclass
class MiniConfig:
    enabled:       bool = field(default_factory=lambda: cfg.MINI_ENABLED)
    autostart:     bool = field(default_factory=lambda: cfg.MINI_AUTOSTART)

    arch:          str  = field(default_factory=lambda: cfg.MINI_ARCH)
    hidden:        int  = field(default_factory=lambda: cfg.MINI_HIDDEN)
    layers:        int  = field(default_factory=lambda: cfg.MINI_LAYERS)
    heads:         int  = field(default_factory=lambda: cfg.MINI_HEADS)
    max_seq:       int  = field(default_factory=lambda: cfg.MINI_MAX_SEQ)
    vocab_size:    int  = field(default_factory=lambda: cfg.MINI_VOCAB)

    attention:     str  = field(default_factory=lambda: cfg.MINI_ATTENTION)
    window:        int  = field(default_factory=lambda: cfg.MINI_WINDOW)

    strategy:      str  = field(default_factory=lambda: cfg.MINI_STRATEGY)
    lr:            float = field(default_factory=lambda: cfg.MINI_LR)
    buffer_size:   int  = field(default_factory=lambda: cfg.MINI_BUFFER_SIZE)
    batch_size:    int  = field(default_factory=lambda: cfg.MINI_BATCH_SIZE)
    yield_ms:      int  = field(default_factory=lambda: cfg.MINI_YIELD_MS)
    nice:          int  = field(default_factory=lambda: cfg.MINI_NICE)
    ckpt_interval: int  = field(default_factory=lambda: cfg.MINI_CKPT_INTERVAL)
    state_dir:     str  = field(default_factory=lambda: cfg.MINI_STATE_DIR)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply partial updates. Returns {applied: [keys], requires_rebuild: bool}.
        Unknown keys are silently dropped.
        """
        valid = {f.name for f in fields(self)}
        applied = []
        requires_rebuild = False
        for k, v in patch.items():
            if k not in valid:
                continue
            setattr(self, k, v)
            applied.append(k)
            if k in REBUILD_FIELDS:
                requires_rebuild = True
        return {"applied": applied, "requires_rebuild": requires_rebuild}
