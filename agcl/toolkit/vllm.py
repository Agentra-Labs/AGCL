"""
vLLM adapter.

vLLM exposes a strict OpenAI-compatible /v1/chat/completions endpoint
plus /v1/models and a few server-level routes (/health, /metrics).
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .openai_compat import ChatEndpoint, OpenAICompatClient


def host() -> str:
    return os.getenv("VLLM_HOST", "http://localhost:8000").rstrip("/")


def endpoint() -> ChatEndpoint:
    return ChatEndpoint(
        name="vllm",
        base_url=f"{host()}/v1",
        api_key=os.getenv("VLLM_API_KEY", "EMPTY"),
    )


def default_model() -> str:
    return os.getenv("VLLM_MODEL", "default")


def client() -> OpenAICompatClient:
    return OpenAICompatClient(endpoint())


async def metrics() -> str:
    """Prometheus-format /metrics. Useful for autoscaling decisions."""
    async with httpx.AsyncClient(timeout=15) as h:
        r = await h.get(f"{host()}/metrics")
        r.raise_for_status()
        return r.text


async def server_health() -> Dict[str, Any]:
    """vLLM's own /health route — different from /v1/models."""
    async with httpx.AsyncClient(timeout=10) as h:
        r = await h.get(f"{host()}/health")
        return {"status": r.status_code, "ok": r.status_code == 200}


async def ping() -> Dict[str, Any]:
    try:
        async with client() as c:
            res = await c.ping()
        srv = await server_health()
        return {"ok": res["ok"] and srv["ok"], "host": host(), **res, "server": srv}
    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        return {"ok": False, "host": host(), "error": str(e)}


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
