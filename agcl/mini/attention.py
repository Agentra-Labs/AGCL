"""
Attention mask presets for the mini transformer.

Each preset is a function that returns an additive mask of shape
[T, T] (or [1, 1, T, T]) where entries set to -inf will be ignored by
the softmax. The mini model picks one based on MiniConfig.attention.

Presets:
  causal   - lower-triangular (the standard autoregressive mask)
  full     - bidirectional (encoder-style)
  sliding  - causal + last `window` tokens only ("local attention")
  banded   - bidirectional within +/- window/2 of each position
  dilated  - causal but only attend to positions {i - 1, i - 2, i - 4, i - 8 ...}
             up to `window` strides; sparse pattern, cheap

The returned tensor is broadcasted with the attention scores; -inf
masks the disallowed pairs and 0.0 keeps them.
"""

from __future__ import annotations

import math
from typing import Callable, Dict

import torch


def _causal(T: int, _window: int) -> torch.Tensor:
    m = torch.zeros(T, T)
    m.masked_fill_(torch.triu(torch.ones(T, T, dtype=torch.bool), diagonal=1),
                   float("-inf"))
    return m


def _full(T: int, _window: int) -> torch.Tensor:
    return torch.zeros(T, T)


def _sliding(T: int, window: int) -> torch.Tensor:
    """Causal + only the most recent `window` tokens are visible."""
    m = _causal(T, window)
    if window > 0 and window < T:
        idx = torch.arange(T)
        too_old = (idx.unsqueeze(0) < (idx.unsqueeze(1) - window))
        m = m.masked_fill(too_old, float("-inf"))
    return m


def _banded(T: int, window: int) -> torch.Tensor:
    """Bidirectional within +/- window/2 of each position."""
    half = max(1, window // 2)
    idx = torch.arange(T)
    dist = (idx.unsqueeze(0) - idx.unsqueeze(1)).abs()
    m = torch.zeros(T, T)
    m.masked_fill_(dist > half, float("-inf"))
    return m


def _dilated(T: int, window: int) -> torch.Tensor:
    """Causal + only positions at strides {1, 2, 4, 8, ...} <= window."""
    m = torch.full((T, T), float("-inf"))
    strides = []
    s = 1
    while s <= max(1, window):
        strides.append(s)
        s *= 2
    for i in range(T):
        m[i, i] = 0.0          # self
        for s in strides:
            j = i - s
            if j >= 0:
                m[i, j] = 0.0
    return m


ATTENTION_MASKS: Dict[str, Callable[[int, int], torch.Tensor]] = {
    "causal":  _causal,
    "full":    _full,
    "sliding": _sliding,
    "banded":  _banded,
    "dilated": _dilated,
}


def build_attention_mask(name: str, T: int, window: int = 64) -> torch.Tensor:
    """Return [T, T] additive mask for the named preset."""
    fn = ATTENTION_MASKS.get(name, _causal)
    return fn(T, window)
