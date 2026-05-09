"""
HuggingFace Text-Generation-Inference adapter.

TGI is in maintenance mode (Dec 2025) but still widely deployed; we
keep this adapter for compatibility. New deployments should use vLLM.

TGI's OpenAI-compat path lives at the root, not under /v1, so the
ChatEndpoint base URL is `<host>/v1` (which TGI maps internally to
`/v1/chat/completions` and `/v1/models`).
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .openai_compat import ChatEndpoint, OpenAICompatClient


def host() -> str:
    return os.getenv("TGI_HOST", "http://localhost:8080").rstrip("/")


def endpoint() -> ChatEndpoint:
    return ChatEndpoint(
        name="tgi",
        base_url=f"{host()}/v1",
        api_key=os.getenv("HUGGING_FACE_HUB_TOKEN", "EMPTY"),
    )


def default_model() -> str:
    return os.getenv("TGI_MODEL", "tgi")


def client() -> OpenAICompatClient:
    return OpenAICompatClient(endpoint())


async def server_info() -> Dict[str, Any]:
    """TGI native /info — model id, max seq, dtype."""
    async with httpx.AsyncClient(timeout=15) as h:
        r = await h.get(f"{host()}/info")
        r.raise_for_status()
        return r.json()


async def ping() -> Dict[str, Any]:
    try:
        info = await server_info()
        return {"ok": True, "host": host(),
                "model": info.get("model_id"),
                "max_input_length": info.get("max_input_length")}
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
