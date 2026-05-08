"""
RecursiveMAS: the loop controller.

Per round r in [1, n_rounds], for each agent a_i in order:

    if (r, i) == (0, 0):
        latent = a_0.forward_latent(input)              # cold start
    else:
        if i == 0:
            injected = round_wrap_outer(prev_latent)    # last agent of prev round -> first agent
        else:
            injected = outer_{i-1 -> i}(prev_latent)    # cross-agent transfer
        latent, _ = a_i.forward_latent(input, injected)

    latent = inner_i(latent)                            # same-agent latent step
    prev_latent = latent

After all rounds, the final agent decodes text from prev_latent (used as
an injected virtual token at the start of its input).

Two modes
---------
- run_latent / generate              (token mode; HF agents only — used for training)
- run_latent_text / generate_text    (text mode; mix HF and GGUF agents freely)

Notes
-----
- `outer` has len(agents) entries: indices 0..N-2 map agent i -> agent i+1,
  and the last entry is the round wrap (agent N-1 -> agent 0). With one
  agent the wrap is the only outer link.
- A backend that returns supports_injection() == False (gguf) silently
  drops the injected latent at its own input. The latent still flows
  through the loop because OuterLink/InnerLink act on the gguf agent's
  own output embedding.
"""

from __future__ import annotations

from typing import List, Union

import torch
import torch.nn as nn

from .links import InnerLink, OuterLink


TextLike = Union[str, List[str]]


class RecursiveMAS(nn.Module):
    def __init__(self, agents, n_rounds: int = 2):
        super().__init__()
        if len(agents) < 1:
            raise ValueError("RecursiveMAS needs at least one agent")
        if n_rounds < 1:
            raise ValueError("n_rounds must be >= 1")
        self.agents = nn.ModuleList(agents)
        self.n_rounds = int(n_rounds)

        dims = [int(a.hidden_size) for a in agents]
        self.dims = dims

        self.inner = nn.ModuleList([InnerLink(d) for d in dims])

        outer = []
        for i in range(len(agents) - 1):
            outer.append(OuterLink(dims[i], dims[i + 1]))
        outer.append(OuterLink(dims[-1], dims[0]))   # round wrap
        self.outer = nn.ModuleList(outer)

    # -------- token-mode (HF only) --------
    def run_latent(self, input_ids, attention_mask=None):
        latent, logits = None, None
        for r in range(self.n_rounds):
            for i, agent in enumerate(self.agents):
                cold = (r == 0 and i == 0)
                injected = None if cold else (
                    self.outer[-1](latent) if i == 0 else self.outer[i - 1](latent)
                )
                if injected is not None and not agent.supports_injection():
                    injected = None
                latent, logits = agent.forward_latent(
                    input_ids, injected=injected, attention_mask=attention_mask,
                )
                latent = self.inner[i](latent)
        return latent, logits

    @torch.no_grad()
    def generate(self, input_ids, attention_mask=None, max_new_tokens: int = 128, **gen_kwargs):
        latent, _ = self.run_latent(input_ids, attention_mask)
        return self.agents[-1].decode(
            input_ids=input_ids, attention_mask=attention_mask,
            injected=latent if self.agents[-1].supports_injection() else None,
            max_new_tokens=max_new_tokens, **gen_kwargs,
        )

    # -------- text-mode (mixed HF + GGUF) --------
    def run_latent_text(self, text: TextLike):
        """
        Same loop, but each agent tokenizes the input text with its own
        tokenizer (or processes raw text for gguf). Returns (latent, logits)
        where logits is None if the final agent is a gguf backend.
        """
        latent, logits = None, None
        for r in range(self.n_rounds):
            for i, agent in enumerate(self.agents):
                cold = (r == 0 and i == 0)
                injected = None if cold else (
                    self.outer[-1](latent) if i == 0 else self.outer[i - 1](latent)
                )
                if injected is not None and not agent.supports_injection():
                    injected = None
                latent, logits = agent.forward_latent_text(text, injected=injected)
                latent = self.inner[i](latent)
        return latent, logits

    @torch.no_grad()
    def generate_text(self, text: TextLike, max_new_tokens: int = 128, **gen_kwargs) -> str:
        latent, _ = self.run_latent_text(text)
        final = self.agents[-1]
        injected = latent if final.supports_injection() else None
        return final.decode_text(text, injected=injected,
                                  max_new_tokens=max_new_tokens, **gen_kwargs)
