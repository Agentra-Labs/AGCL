"""
agcl.toolkit — pluggable infra/inference adapters.

Each module wraps one external system (LiteLLM gateway, Ollama, vLLM,
Redis, S3, Cloudflare relay, Discord, etc.) behind a small uniform
surface so AGCL doesn't have to care which backend is actually running.

The toolkit subsystem is opt-in: nothing here is loaded eagerly at
import time. Modules are imported on demand by `endpoints.py`, by
`registry.discover()`, or by direct calls from agent code.

Public surface:
    discover()              -> dict of {name: status} for every adapter
    get_chat_client()       -> async OpenAI-compatible client (gateway-aware)
    state_store()           -> redis store if AGCL_REDIS_URL else local fallback
    checkpoint_store()      -> S3 store if AGCL_S3_BUCKET else local fallback
"""

from __future__ import annotations

from .registry import (
    discover,
    get_chat_client,
    state_store,
    checkpoint_store,
)

__all__ = [
    "discover",
    "get_chat_client",
    "state_store",
    "checkpoint_store",
]
