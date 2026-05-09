"""
LiteLLM gateway adapter.

Two ways to use it:

    1. Native LiteLLM SDK (when `litellm` is pip-installed). This gives
       routing, fallbacks, virtual keys, spend tracking — all the proxy
       features in-process.

    2. Plain OpenAI-compatible HTTP via the proxy at AGCL_LLM_BASE_URL.
       Works without the SDK installed.

The HTTP path is the default because it adds zero deps to AGCL.
SDK path is opt-in via `use_sdk=True`.

This module never imports `litellm` at module load time, so AGCL still
boots when the SDK is missing.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional

from .openai_compat import ChatEndpoint, OpenAICompatClient


def endpoint() -> ChatEndpoint:
    base = os.getenv("AGCL_LLM_BASE_URL", "http://localhost:4000").rstrip("/")
    return ChatEndpoint(
        name="litellm",
        base_url=f"{base}/v1",
        api_key=os.getenv("AGCL_LLM_API_KEY", "sk-agcl-internal"),
    )


def default_model() -> str:
    return os.getenv("AGCL_LLM_MODEL", "smart")


def client() -> OpenAICompatClient:
    return OpenAICompatClient(endpoint())


async def chat(messages: List[Dict[str, Any]], *,
                model: Optional[str] = None,
                stream: bool = False,
                max_tokens: Optional[int] = None,
                temperature: Optional[float] = None,
                ) -> Any:
    """
    Convenience wrapper. Returns:
        - dict (OpenAI response) when stream=False
        - async iterator of text deltas when stream=True
    """
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


async def ping() -> Dict[str, Any]:
    async with client() as c:
        return {"endpoint": c.endpoint.base_url, **(await c.ping())}


# ----------------------------------------------------------------------
# Native SDK path — only used when caller asks for it.
# ----------------------------------------------------------------------

def sdk_completion(messages: List[Dict[str, Any]], *,
                    model: str,
                    **kwargs: Any) -> Dict[str, Any]:
    """
    Direct call into the LiteLLM SDK. Raises ImportError if the package
    isn't installed. Use this when you want LiteLLM's in-process router
    (latency-based routing, fallback chains) instead of the proxy.
    """
    import litellm  # type: ignore
    return litellm.completion(model=model, messages=messages, **kwargs)
