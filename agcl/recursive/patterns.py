"""
Collaboration patterns built on the recursive MAS.

The paper says the same recursive mechanism is reused across patterns —
only the agent roles and the recursion depth change. Each builder here
returns a configured RecursiveMAS; agents are produced by an
agent_factory(role: str) callable so the caller can pick whatever
underlying model they want.
"""

from __future__ import annotations

from typing import Callable

from .agent import RecursiveAgent
from .mas import RecursiveMAS


def sequential(agent_factory: Callable[[str], RecursiveAgent],
               roles=("planner", "critic", "solver"),
               n_rounds: int = 2) -> RecursiveMAS:
    """Sequential planner -> critic -> solver pipeline."""
    agents = [agent_factory(role=r) for r in roles]
    return RecursiveMAS(agents, n_rounds=n_rounds)


def mixture_of_experts(agent_factory: Callable[[str], RecursiveAgent],
                       n_experts: int = 3,
                       n_rounds: int = 2) -> RecursiveMAS:
    """N specialized experts threaded through the loop."""
    if n_experts < 1:
        raise ValueError("n_experts must be >= 1")
    agents = [agent_factory(role=f"expert_{i}") for i in range(n_experts)]
    return RecursiveMAS(agents, n_rounds=n_rounds)


def distillation(teacher_factory: Callable[[str], RecursiveAgent],
                 student_factory: Callable[[str], RecursiveAgent],
                 n_rounds: int = 2) -> RecursiveMAS:
    """Teacher (expert) -> student (learner)."""
    agents = [teacher_factory(role="teacher"), student_factory(role="student")]
    return RecursiveMAS(agents, n_rounds=n_rounds)


def deliberation(agent_factory: Callable[[str], RecursiveAgent],
                 n_rounds: int = 3) -> RecursiveMAS:
    """Reflector + tool-caller, multiple rounds for back-and-forth deliberation."""
    agents = [agent_factory(role="reflector"), agent_factory(role="tool_caller")]
    return RecursiveMAS(agents, n_rounds=n_rounds)
