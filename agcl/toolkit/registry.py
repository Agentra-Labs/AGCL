"""
Central registry of toolkit adapters.

Reads env vars on first call and caches the resulting clients/stores
for the rest of the process. Hot-reload by calling `reset()`.

Resolution order for a chat client:
    1. AGCL_LLM_BASE_URL set     -> LiteLLM gateway
    2. VLLM_HOST set             -> direct vLLM
    3. OLLAMA_HOST set           -> direct Ollama
    4. None                      -> openai/anthropic via agcl.cloud (legacy)

Resolution order for state store:
    1. AGCL_REDIS_URL set        -> Redis/Valkey
    2. None                      -> agcl.state (local JSON files)

Resolution order for checkpoint store:
    1. AGCL_S3_BUCKET set        -> S3-compatible (R2/MinIO/B2/AWS)
    2. None                      -> local filesystem (state_dir/topics/)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


# Cached singletons. The chat-client singleton was removed (httpx
# pools must close inside the loop that opened them); state_store and
# checkpoint_store stay cached because they're file-system-backed and
# safe to keep across loops.
_CACHE: Dict[str, Any] = {
    "state_store": None,
    "checkpoint_store": None,
}


def reset() -> None:
    """Force the next lookup to rebuild from current env vars."""
    _CACHE.update({k: None for k in _CACHE})


# ----------------------------------------------------------------------
# Chat client (gateway-aware)
# ----------------------------------------------------------------------

def _select_chat_endpoint() -> Optional[Dict[str, str]]:
    """Returns {name, base_url, api_key} or None for legacy fallback."""
    if os.getenv("AGCL_LLM_BASE_URL"):
        return {
            "name":     "litellm",
            "base_url": os.environ["AGCL_LLM_BASE_URL"].rstrip("/") + "/v1",
            "api_key":  os.getenv("AGCL_LLM_API_KEY", "sk-agcl-internal"),
            "model":    os.getenv("AGCL_LLM_MODEL", "smart"),
        }
    if os.getenv("VLLM_HOST"):
        return {
            "name":     "vllm",
            "base_url": os.environ["VLLM_HOST"].rstrip("/") + "/v1",
            "api_key":  os.getenv("VLLM_API_KEY", "EMPTY"),
            "model":    os.getenv("VLLM_MODEL", "default"),
        }
    if os.getenv("OLLAMA_HOST"):
        return {
            "name":     "ollama",
            "base_url": os.environ["OLLAMA_HOST"].rstrip("/") + "/v1",
            "api_key":  "ollama",
            "model":    os.getenv("OLLAMA_MODEL", "llama3"),
        }
    return None


def get_chat_client():
    """
    Return a fresh OpenAICompatClient (or None if no gateway is
    configured). Callers OWN the returned client and must call
    `await client.aclose()` when done — `async with client: ...` is the
    cleanest pattern.

    We deliberately don't cache: the underlying httpx.AsyncClient owns
    a connection pool that must be closed inside the same asyncio loop
    that opened it. Caching across calls makes that ordering fragile
    and is the source of "Event loop is closed" tracebacks at process
    exit. Reconnecting per call is cheap (one TCP handshake; the wire
    protocol is HTTP/1.1 keep-alive within a single stream anyway).
    """
    sel = _select_chat_endpoint()
    if sel is None:
        return None
    from .openai_compat import ChatEndpoint, OpenAICompatClient
    ep = ChatEndpoint(name=sel["name"], base_url=sel["base_url"],
                      api_key=sel["api_key"])
    client = OpenAICompatClient(ep)
    # Stash the resolved default model on the client for callers that
    # don't explicitly pass one.
    client.default_model = sel["model"]  # type: ignore[attr-defined]
    return client


# ----------------------------------------------------------------------
# State store (Redis or local)
# ----------------------------------------------------------------------

def state_store():
    if _CACHE["state_store"] is not None:
        return _CACHE["state_store"]
    if os.getenv("AGCL_REDIS_URL"):
        from .redis_state import RedisStateStore
        store = RedisStateStore(os.environ["AGCL_REDIS_URL"])
    else:
        from .redis_state import LocalStateStore
        store = LocalStateStore()
    _CACHE["state_store"] = store
    return store


# ----------------------------------------------------------------------
# Checkpoint store (S3 or local)
# ----------------------------------------------------------------------

def checkpoint_store():
    if _CACHE["checkpoint_store"] is not None:
        return _CACHE["checkpoint_store"]
    if os.getenv("AGCL_S3_BUCKET"):
        from .s3_store import S3CheckpointStore
        store = S3CheckpointStore()
    else:
        from .s3_store import LocalCheckpointStore
        store = LocalCheckpointStore()
    _CACHE["checkpoint_store"] = store
    return store


# ----------------------------------------------------------------------
# Discovery — what's available right now, with no side effects
# ----------------------------------------------------------------------

def discover() -> Dict[str, Any]:
    """
    Return a snapshot of which toolkit adapters are configured + which
    ones import successfully. Pure inspection — does NOT open sockets.
    Call ping() on individual modules for a live health check.
    """
    out: Dict[str, Any] = {}

    def _check(name: str, env: Dict[str, str], importable: str) -> Dict[str, Any]:
        present = {k: bool(os.getenv(k)) for k in env}
        try:
            __import__(importable)
            imp_ok, imp_err = True, None
        except ImportError as e:
            imp_ok, imp_err = False, str(e)
        return {
            "configured": all(present.values()),
            "env":        present,
            "importable": imp_ok,
            "import_err": imp_err,
        }

    out["litellm"]   = _check("litellm",   {"AGCL_LLM_BASE_URL": "url"},  "agcl.toolkit.litellm_gw")
    out["ollama"]    = _check("ollama",    {"OLLAMA_HOST": "url"},        "agcl.toolkit.ollama")
    out["vllm"]      = _check("vllm",      {"VLLM_HOST": "url"},          "agcl.toolkit.vllm")
    out["tgi"]       = _check("tgi",       {"TGI_HOST": "url"},           "agcl.toolkit.tgi")
    out["redis"]     = _check("redis",     {"AGCL_REDIS_URL": "url"},     "agcl.toolkit.redis_state")
    out["s3"]        = _check("s3",        {"AGCL_S3_BUCKET": "bucket"},  "agcl.toolkit.s3_store")
    out["cloudflare"] = _check("cloudflare", {"AGCL_RELAY_URL": "url"},   "agcl.toolkit.cloudflare")
    out["discord"]   = _check("discord",   {"DISCORD_BOT_TOKEN": "token"}, "agcl.toolkit.discord")
    out["webrtc"]    = _check("webrtc",    {"AGCL_WEBRTC_ENABLED": "flag"}, "agcl.toolkit.webrtc")
    out["gcp"]       = _check("gcp",       {"GOOGLE_CLOUD_PROJECT": "project"}, "agcl.toolkit.gcp")
    return out
