"""
The two projection modules from RecursiveMAS.

Both are 2-layer residual MLPs. The residual is what makes training stable
across the unrolled loop: gradients flow through the identity branch even
when the MLP output is near zero, which it is at initialization (W2 starts
at zero so the link is exactly the identity mapping at step 0).

    InnerLink (same agent, next latent thought)
        R_in(h) = h + W2 GELU(W1 h)

    OuterLink (agent A -> agent B; can change dim)
        R_out(h) = W3 h + W2 GELU(W1 h)
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class InnerLink(nn.Module):
    """Same-agent latent recurrence."""

    def __init__(self, dim: int, hidden_dim: int | None = None):
        super().__init__()
        hidden_dim = hidden_dim or dim
        self.dim = dim
        self.hidden_dim = hidden_dim
        self.W1 = nn.Linear(dim, hidden_dim, bias=False)
        self.W2 = nn.Linear(hidden_dim, dim, bias=False)
        nn.init.xavier_uniform_(self.W1.weight)
        nn.init.zeros_(self.W2.weight)

    def forward(self, h: torch.Tensor) -> torch.Tensor:
        return h + self.W2(F.gelu(self.W1(h)))


class OuterLink(nn.Module):
    """Cross-agent transfer; W3 handles dim mismatch (and starts at identity when dims match)."""

    def __init__(self, in_dim: int, out_dim: int, hidden_dim: int | None = None):
        super().__init__()
        hidden_dim = hidden_dim or max(in_dim, out_dim)
        self.in_dim = in_dim
        self.out_dim = out_dim
        self.hidden_dim = hidden_dim
        self.W1 = nn.Linear(in_dim, hidden_dim, bias=False)
        self.W2 = nn.Linear(hidden_dim, out_dim, bias=False)
        self.W3 = nn.Linear(in_dim, out_dim, bias=False)
        nn.init.xavier_uniform_(self.W1.weight)
        nn.init.zeros_(self.W2.weight)
        if in_dim == out_dim:
            nn.init.eye_(self.W3.weight)
        else:
            nn.init.xavier_uniform_(self.W3.weight)

    def forward(self, h: torch.Tensor) -> torch.Tensor:
        return self.W3(h) + self.W2(F.gelu(self.W1(h)))
