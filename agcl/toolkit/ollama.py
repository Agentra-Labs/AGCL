"""
Ollama adapter.

Ollama exposes both:
    - native API at /api/generate, /api/tags, /api/show, /api/pull
    - OpenAI-compat at /v1/chat/completions, /v1/models

We use the OpenAI-compat path for chat (uniform with vLLM/TGI/LiteLLM)
and the native path for model management (pull/list/show — these are
ollama-specific and not in the OpenAI spec).
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .openai_compat import ChatEndpoint, OpenAICompatClient


def host() -> str:
    return os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def endpoint() -> ChatEndpoint:
    return ChatEndpoint(
        name="ollama",
        base_url=f"{host()}/v1",
        api_key="ollama",
    )


def default_model() -> str:
    return os.getenv("OLLAMA_MODEL", "llama3")


def client() -> OpenAICompatClient:
    return OpenAICompatClient(endpoint())


# ------------------------------------------------------------------
# Native API (model management)
# ------------------------------------------------------------------

async def list_local_models() -> List[Dict[str, Any]]:
    """Return Ollama's /api/tags — the models actually pulled to disk."""
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.get(f"{host()}/api/tags")
        r.raise_for_status()
        return r.json().get("models", [])


async def show_model(name: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as h:
        r = await h.post(f"{host()}/api/show", json={"name": name})
        r.raise_for_status()
        return r.json()


async def pull_model(name: str) -> AsyncIterator[Dict[str, Any]]:
    """
    Stream pull progress events. Each event looks like:
        {"status": "pulling 8eeb52dfb3bb...", "digest": "...",
         "total": 1234, "completed": 456}
    The final event has {"status": "success"}.
    """
    async with httpx.AsyncClient(timeout=None) as h:
        async with h.stream(
            "POST", f"{host()}/api/pull", json={"name": name},
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                import json
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


async def ping() -> Dict[str, Any]:
    """Health check: hits /api/tags (cheap, no model load)."""
    try:
        models = await list_local_models()
        return {"ok": True, "host": host(), "models": len(models)}
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return {"ok": False, "host": host(), "error": str(e)}


# ------------------------------------------------------------------
# Convenience: chat / stream via OpenAI-compat
# ------------------------------------------------------------------

async def chat(messages: List[Dict[str, Any]], *,
                model: Optional[str] = None,
                stream: bool = False,
                max_tokens: Optional[int] = None,
                temperature: Optional[float] = None,
                ) -> Any:
    model = model or default_model()
    c = client()
    if stream:
        async def _it() -> AsyncIterator[str]:
            try:
                async for chunk in c.stream(
                    messages, model=model,
                    max_tokens=max_tokens, temperature=temperature,
                ):
                    yield chunk
            finally:
                await c.aclose()
        return _it()
    try:
        return await c.chat(
            messages, model=model,
            max_tokens=max_tokens, temperature=temperature,
        )
    finally:
        await c.aclose()
