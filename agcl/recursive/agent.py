"""
RecursiveAgent: a thin wrapper that adapts a backend (HF or GGUF) to the
shape RecursiveMAS expects.

The class is constructed in one of three ways:

    RecursiveAgent(backend, role="...")         # explicit backend
    RecursiveAgent(model, tokenizer, role)      # legacy HF path
    RecursiveAgent.from_pretrained(name, ...)   # HF download / load
    RecursiveAgent.from_gguf(path, ...)         # gguf load
"""

from __future__ import annotations

from typing import Optional, Union

import torch
import torch.nn as nn

from .backends import _BackendBase, HFBackend, GGUFBackend


class RecursiveAgent(nn.Module):
    def __init__(self, model_or_backend=None, tokenizer=None, role: str = "",
                 *, backend: Optional[_BackendBase] = None):
        super().__init__()
        if backend is None:
            if isinstance(model_or_backend, _BackendBase):
                backend = model_or_backend
            elif model_or_backend is not None:
                backend = HFBackend(model_or_backend, tokenizer)
            else:
                raise ValueError("RecursiveAgent needs a backend or an HF model")
        self.backend = backend
        self.role = role

    # ---- attribute passthroughs ----
    @property
    def hidden_size(self) -> int:
        return self.backend.hidden_size

    @property
    def model(self):
        return getattr(self.backend, "model", None)

    @property
    def tokenizer(self):
        return getattr(self.backend, "tokenizer", None)

    def supports_injection(self) -> bool:
        return self.backend.supports_injection()

    # ---- token-mode (HF only) ----
    def encode(self, text):
        return self.backend.encode(text)

    def forward_latent(self, input_ids, injected=None, attention_mask=None):
        return self.backend.forward_latent(input_ids, injected, attention_mask)

    @torch.no_grad()
    def decode(self, input_ids=None, attention_mask=None, injected=None,
               max_new_tokens: int = 128, **gen_kwargs):
        # Used by stage-2 evaluation / tests that pre-tokenize. Routes to HF.
        if not isinstance(self.backend, HFBackend):
            raise RuntimeError("decode(input_ids=...) requires an HF backend; use decode_text()")
        if injected is None:
            return self.backend.model.generate(
                input_ids=input_ids, attention_mask=attention_mask,
                max_new_tokens=max_new_tokens, **gen_kwargs,
            )
        embed = self.backend.model.get_input_embeddings()
        e = embed(input_ids)
        inj = injected.unsqueeze(1).to(e.dtype)
        e = torch.cat([inj, e], dim=1)
        if attention_mask is not None:
            pad = torch.ones(
                attention_mask.size(0), 1,
                device=attention_mask.device, dtype=attention_mask.dtype,
            )
            attention_mask = torch.cat([pad, attention_mask], dim=1)
        return self.backend.model.generate(
            inputs_embeds=e, attention_mask=attention_mask,
            max_new_tokens=max_new_tokens, **gen_kwargs,
        )

    # ---- text-mode (works for both backends) ----
    def forward_latent_text(self, text, injected=None):
        return self.backend.forward_latent_text(text, injected)

    @torch.no_grad()
    def decode_text(self, prompt_text, injected=None,
                    max_new_tokens: int = 128, **gen_kwargs) -> str:
        return self.backend.decode_text(prompt_text, injected,
                                         max_new_tokens, **gen_kwargs)

    # ---- factories ----
    @classmethod
    def from_pretrained(cls, name_or_path: str, role: str = "",
                        dtype=None, device: str = "cpu"):
        return cls(HFBackend.load(name_or_path, dtype=dtype, device=device), role=role)

    @classmethod
    def from_gguf(cls, model_path: str, role: str = "",
                  n_ctx: int = 2048, n_gpu_layers: int = 0, n_threads: int = 4):
        return cls(
            GGUFBackend.load(model_path, n_ctx=n_ctx,
                             n_gpu_layers=n_gpu_layers, n_threads=n_threads),
            role=role,
        )
