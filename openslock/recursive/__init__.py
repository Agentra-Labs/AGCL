"""
RecursiveMAS — recursive multi-agent reasoning in latent space.

Instead of agents passing full text between each other every round, the
recursive MAS keeps reasoning in embedding space. Two small projection
modules are the only things between agents:

    InnerLink:  same-agent latent thought recurrence  (R_in(h) = h + W2 GELU(W1 h))
    OuterLink:  cross-agent latent transfer           (R_out(h) = W3 h + W2 GELU(W1 h))

The loop unrolls n_rounds times across all agents, and only the final
agent decodes text once at the very end.

Public API:
    InnerLink, OuterLink                  — projection modules
    RecursiveAgent                         — wraps a causal LM, exposes hidden states
    RecursiveMAS                           — the loop controller
    sequential, mixture_of_experts,
    distillation, deliberation             — collaboration pattern builders
    stage1_warmup_inner, stage2_full_loop  — offline training entry points
    auto_train, bootstrap_pairs,
    cloud_confirm_switch, TopicTracker     — cloud-teacher online training
    RecursiveSession                       — multi-turn driver
"""

from .links import InnerLink, OuterLink
from .backends import HFBackend, GGUFBackend
from .agent import RecursiveAgent
from .mas import RecursiveMAS
from .patterns import sequential, mixture_of_experts, distillation, deliberation
from .train import stage1_warmup_inner, stage2_full_loop
from .builder import build_agent, build_mas_from_specs, build_from_config
from .auto_train import (
    auto_train, bootstrap_pairs, cloud_confirm_switch, TopicTracker,
)
from .session import RecursiveSession
from . import persistence

__all__ = [
    "InnerLink", "OuterLink",
    "HFBackend", "GGUFBackend",
    "RecursiveAgent", "RecursiveMAS",
    "sequential", "mixture_of_experts", "distillation", "deliberation",
    "stage1_warmup_inner", "stage2_full_loop",
    "build_agent", "build_mas_from_specs", "build_from_config",
    "auto_train", "bootstrap_pairs", "cloud_confirm_switch", "TopicTracker",
    "RecursiveSession",
    "persistence",
]
