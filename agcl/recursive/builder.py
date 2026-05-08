"""
Build a RecursiveMAS from config.

Reads MAS_AGENTS / MAS_PATTERN / MAS_ROUNDS / MAS_DEVICE / MAS_DTYPE from
agcl.config and instantiates the right backends.

Models are loaded lazily, one per spec. If two specs point at the same
model path/name they will load it twice — that is intentional, since each
agent must hold its own state. If you want shared weights, pass a single
spec and bump MAS_ROUNDS instead.
"""

from __future__ import annotations

from typing import List, Dict, Any

import torch

from .agent import RecursiveAgent
from .mas import RecursiveMAS
from . import patterns as P


_DTYPES = {
    "float32": torch.float32, "fp32": torch.float32,
    "float16": torch.float16, "fp16": torch.float16, "half": torch.float16,
    "bfloat16": torch.bfloat16, "bf16": torch.bfloat16,
}


def _resolve_dtype(name: str):
    return _DTYPES.get((name or "float32").lower())


def build_agent(spec: Dict[str, Any], default_device: str = "cpu",
                default_dtype: str = "float32") -> RecursiveAgent:
    """Instantiate a single RecursiveAgent from a config dict."""
    backend = spec.get("backend", "hf").lower()
    role    = spec.get("role", "")
    model   = spec.get("model") or spec.get("model_path") or spec.get("name")
    if not model:
        raise ValueError(f"agent spec missing 'model': {spec}")

    if backend == "hf":
        return RecursiveAgent.from_pretrained(
            model,
            role=role,
            dtype=_resolve_dtype(spec.get("dtype", default_dtype)),
            device=spec.get("device", default_device),
        )
    elif backend == "gguf":
        return RecursiveAgent.from_gguf(
            model,
            role=role,
            n_ctx=int(spec.get("n_ctx", 2048)),
            n_gpu_layers=int(spec.get("n_gpu_layers", 0)),
            n_threads=int(spec.get("n_threads", 4)),
        )
    else:
        raise ValueError(f"unknown backend {backend!r} (use 'hf' or 'gguf')")


def build_mas_from_specs(specs: List[Dict[str, Any]],
                          n_rounds: int = 2,
                          pattern: str = "sequential",
                          default_device: str = "cpu",
                          default_dtype: str = "float32") -> RecursiveMAS:
    """
    Build a RecursiveMAS by instantiating each spec and arranging it
    according to `pattern`. Pattern templates only affect agent ordering
    metadata — the loop wiring is shared across all of them.
    """
    if not specs:
        raise ValueError("no agent specs supplied")

    def _make(spec, role_override: str):
        new = dict(spec)
        if role_override:
            new["role"] = role_override
        return build_agent(new, default_device=default_device,
                           default_dtype=default_dtype)

    if pattern == "sequential":
        roles = [s.get("role", f"agent_{i}") for i, s in enumerate(specs)]
        agents = [_make(specs[i], roles[i]) for i in range(len(specs))]
        return RecursiveMAS(agents, n_rounds=n_rounds)

    if pattern == "moe":
        agents = [_make(specs[i], specs[i].get("role", f"expert_{i}"))
                  for i in range(len(specs))]
        return RecursiveMAS(agents, n_rounds=n_rounds)

    if pattern == "distill":
        if len(specs) != 2:
            raise ValueError("distill pattern needs exactly 2 specs (teacher, student)")
        agents = [_make(specs[0], specs[0].get("role", "teacher")),
                  _make(specs[1], specs[1].get("role", "student"))]
        return RecursiveMAS(agents, n_rounds=n_rounds)

    if pattern == "deliberation":
        if len(specs) < 2:
            raise ValueError("deliberation pattern needs at least 2 specs")
        agents = [_make(specs[i], specs[i].get("role", f"agent_{i}"))
                  for i in range(len(specs))]
        return RecursiveMAS(agents, n_rounds=max(n_rounds, 3))

    if pattern == "custom":
        agents = [_make(specs[i], specs[i].get("role", f"agent_{i}"))
                  for i in range(len(specs))]
        return RecursiveMAS(agents, n_rounds=n_rounds)

    raise ValueError(f"unknown pattern {pattern!r} "
                     "(sequential|moe|distill|deliberation|custom)")


def build_from_config() -> RecursiveMAS:
    """Build the MAS using values from agcl.config."""
    from agcl import config as cfg
    return build_mas_from_specs(
        cfg.MAS_AGENTS,
        n_rounds=cfg.MAS_ROUNDS,
        pattern=cfg.MAS_PATTERN,
        default_device=cfg.MAS_DEVICE,
        default_dtype=cfg.MAS_DTYPE,
    )
