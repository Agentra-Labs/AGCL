"""
Mini-model architectures.

Two switchable arches:

  - `MiniModel`  : tiny decoder-only transformer with configurable
                   attention preset (causal / full / sliding / banded /
                   dilated). Trains via next-token CE; supports an
                   optional latent-injection input that nudges its
                   internal hidden states toward a teacher latent.

  - `MiniMLP`    : two-layer MLP that maps a prompt-embedding to a
                   teacher latent. Cannot generate text on its own;
                   useful for cheap latent-distillation only.

Both share the same forward signature shape so the trainer can hold a
generic reference. `build_arch(config)` dispatches.

The transformer is intentionally tiny: 2 layers, 128 hidden, 4 heads
by default. On CPU with batch_size=2 a single step is well under
100 ms, leaving plenty of room for the main MAS work.
"""

from __future__ import annotations

import math
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .attention import build_attention_mask
from .config import MiniConfig


class MiniAttention(nn.Module):
    def __init__(self, hidden: int, heads: int) -> None:
        super().__init__()
        assert hidden % heads == 0, "hidden must divide heads"
        self.heads = heads
        self.head_dim = hidden // heads
        self.qkv = nn.Linear(hidden, 3 * hidden, bias=False)
        self.proj = nn.Linear(hidden, hidden, bias=False)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        B, T, C = x.shape
        qkv = self.qkv(x).reshape(B, T, 3, self.heads, self.head_dim)
        q, k, v = qkv.unbind(2)
        q = q.transpose(1, 2)              # [B, H, T, D]
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)
        att = (q @ k.transpose(-2, -1)) / math.sqrt(self.head_dim)
        att = att + mask                    # broadcast [T, T] -> [B, H, T, T]
        att = att.softmax(dim=-1)
        y = att @ v                         # [B, H, T, D]
        y = y.transpose(1, 2).contiguous().reshape(B, T, C)
        return self.proj(y)


class MiniBlock(nn.Module):
    def __init__(self, hidden: int, heads: int) -> None:
        super().__init__()
        self.ln1 = nn.LayerNorm(hidden)
        self.att = MiniAttention(hidden, heads)
        self.ln2 = nn.LayerNorm(hidden)
        self.mlp = nn.Sequential(
            nn.Linear(hidden, 4 * hidden),
            nn.GELU(),
            nn.Linear(4 * hidden, hidden),
        )

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        x = x + self.att(self.ln1(x), mask)
        x = x + self.mlp(self.ln2(x))
        return x


class MiniModel(nn.Module):
    """Tiny decoder-only transformer with configurable attention."""

    arch_name = "transformer"

    def __init__(self, config: MiniConfig) -> None:
        super().__init__()
        self.config = config
        self.tok_emb = nn.Embedding(config.vocab_size, config.hidden)
        self.pos_emb = nn.Embedding(config.max_seq, config.hidden)
        self.blocks = nn.ModuleList(
            [MiniBlock(config.hidden, config.heads)
             for _ in range(config.layers)]
        )
        self.ln_f = nn.LayerNorm(config.hidden)
        self.lm_head = nn.Linear(config.hidden, config.vocab_size, bias=False)
        # Hook for latent distillation: a learned projection that maps
        # the model's pooled hidden state back into teacher-latent dims.
        # Lazily sized on first use so we don't need teacher dim upfront.
        self.latent_proj: Optional[nn.Linear] = None

        self.register_buffer(
            "_attn_mask",
            build_attention_mask(
                config.attention, config.max_seq, config.window,
            ),
            persistent=False,
        )

    def _mask_for(self, T: int) -> torch.Tensor:
        return self._attn_mask[:T, :T].unsqueeze(0).unsqueeze(0)

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        """ids: [B, T] -> logits [B, T, V]."""
        B, T = ids.shape
        pos = torch.arange(T, device=ids.device)
        x = self.tok_emb(ids) + self.pos_emb(pos)[None, :, :]
        mask = self._mask_for(T).to(x.dtype)
        for blk in self.blocks:
            x = blk(x, mask)
        x = self.ln_f(x)
        return self.lm_head(x)

    def pooled_hidden(self, ids: torch.Tensor) -> torch.Tensor:
        """Last-position hidden state, useful for latent distillation."""
        B, T = ids.shape
        pos = torch.arange(T, device=ids.device)
        x = self.tok_emb(ids) + self.pos_emb(pos)[None, :, :]
        mask = self._mask_for(T).to(x.dtype)
        for blk in self.blocks:
            x = blk(x, mask)
        x = self.ln_f(x)
        return x[:, -1, :]   # [B, hidden]

    def project_to_latent(self, h: torch.Tensor, target_dim: int) -> torch.Tensor:
        if self.latent_proj is None or self.latent_proj.out_features != target_dim:
            # Lazy build, keep on the same device as h.
            self.latent_proj = nn.Linear(self.config.hidden, target_dim).to(h.device)
        return self.latent_proj(h)

    @torch.no_grad()
    def generate(self, ids: torch.Tensor, max_new: int = 32,
                 temperature: float = 0.8) -> torch.Tensor:
        for _ in range(max_new):
            ids_in = ids[:, -self.config.max_seq:]
            logits = self.forward(ids_in)[:, -1, :]
            if temperature > 0:
                probs = F.softmax(logits / temperature, dim=-1)
                nxt = torch.multinomial(probs, 1)
            else:
                nxt = logits.argmax(dim=-1, keepdim=True)
            ids = torch.cat([ids, nxt], dim=1)
        return ids


class MiniMLP(nn.Module):
    """Cheap latent-distillation-only architecture. No text generation."""

    arch_name = "mlp"

    def __init__(self, config: MiniConfig) -> None:
        super().__init__()
        self.config = config
        self.tok_emb = nn.Embedding(config.vocab_size, config.hidden)
        self.net = nn.Sequential(
            nn.Linear(config.hidden, config.hidden * 2),
            nn.GELU(),
            nn.Linear(config.hidden * 2, config.hidden),
        )
        self.lm_head = nn.Linear(config.hidden, config.vocab_size, bias=False)
        self.latent_proj: Optional[nn.Linear] = None

    def _mean_embed(self, ids: torch.Tensor) -> torch.Tensor:
        e = self.tok_emb(ids)              # [B, T, H]
        return e.mean(dim=1)               # [B, H]

    def forward(self, ids: torch.Tensor) -> torch.Tensor:
        # Returns logits at the last position only (broadcast for CE);
        # MLP can't model order so we predict a single next token.
        h = self.net(self._mean_embed(ids))
        # Fake [B, T, V] by tiling so CE callers don't need a special path.
        T = ids.size(1)
        logits = self.lm_head(h).unsqueeze(1).expand(-1, T, -1)
        return logits

    def pooled_hidden(self, ids: torch.Tensor) -> torch.Tensor:
        return self.net(self._mean_embed(ids))

    def project_to_latent(self, h: torch.Tensor, target_dim: int) -> torch.Tensor:
        if self.latent_proj is None or self.latent_proj.out_features != target_dim:
            self.latent_proj = nn.Linear(self.config.hidden, target_dim).to(h.device)
        return self.latent_proj(h)

    @torch.no_grad()
    def generate(self, ids: torch.Tensor, max_new: int = 32,
                 temperature: float = 0.8) -> torch.Tensor:
        # MLP can't really sequence-generate; emit max_new copies of the
        # argmax. Useful only for sanity-checking the head learned anything.
        h = self.net(self._mean_embed(ids))
        logits = self.lm_head(h)
        nxt = logits.argmax(dim=-1, keepdim=True).expand(-1, max_new)
        return torch.cat([ids, nxt], dim=1)


def build_arch(config: MiniConfig) -> nn.Module:
    """Dispatch on config.arch."""
    if config.arch == "mlp":
        return MiniMLP(config)
    return MiniModel(config)
