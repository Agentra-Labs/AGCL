"""
Async chat client for any OpenAI-compatible /v1/chat/completions endpoint.

Used as the shared transport for:
    - LiteLLM proxy        (any model)
    - Ollama               (:11434/v1)
    - vLLM                 (:8000/v1)
    - HuggingFace TGI      (legacy /v1/chat/completions)
    - Anything else that speaks the spec

Design choices:
    - Pure httpx — no openai SDK dep so the toolkit imports cleanly even
      when openai/anthropic aren't installed.
    - Streaming yields raw text deltas (parsed from SSE), matching what
      agcl.cloud.stream_continuation already exposes downstream.
    - 4xx/5xx surfaces as ProviderError with the body text; callers can
      decide whether to fall back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx


class ProviderError(RuntimeError):
    def __init__(self, status: int, body: str, provider: str = "?"):
        super().__init__(f"[{provider}] {status}: {body[:300]}")
        self.status = status
        self.body = body
        self.provider = provider


@dataclass
class ChatEndpoint:
    """One OpenAI-compatible inference target."""
    name:     str               # human label
    base_url: str               # e.g. http://localhost:11434/v1
    api_key:  str = "EMPTY"     # sent as Bearer; required by spec, ignored by Ollama
    timeout:  float = 60.0

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"

    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
        }


class OpenAICompatClient:
    """One client per ChatEndpoint. Reuses an httpx.AsyncClient."""

    def __init__(self, endpoint: ChatEndpoint):
        self.endpoint = endpoint
        self._client = httpx.AsyncClient(timeout=endpoint.timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self): return self
    async def __aexit__(self, *exc): await self.aclose()

    # ------------------------------------------------------------------
    # health / model listing
    # ------------------------------------------------------------------

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            r = await self._client.get(
                self.endpoint.models_url, headers=self.endpoint.headers(),
            )
        except (httpx.ConnectError, httpx.ReadError) as e:
            raise ProviderError(0, str(e), self.endpoint.name) from e
        if r.status_code >= 400:
            raise ProviderError(r.status_code, r.text, self.endpoint.name)
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else body

    async def ping(self) -> Dict[str, Any]:
        """Return {"ok": bool, "models": int, "error": str|None}."""
        try:
            models = await self.list_models()
            return {"ok": True, "models": len(models), "error": None}
        except ProviderError as e:
            return {"ok": False, "models": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # chat completion (blocking + streaming)
    # ------------------------------------------------------------------

    async def chat(self, messages: List[Dict[str, Any]], *,
                    model: str,
                    max_tokens: Optional[int] = None,
                    temperature: Optional[float] = None,
                    extra: Optional[Dict[str, Any]] = None,
                    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if max_tokens is not None:  body["max_tokens"] = max_tokens
        if temperature is not None: body["temperature"] = temperature
        if extra:                   body.update(extra)
        r = await self._client.post(
            self.endpoint.chat_url, headers=self.endpoint.headers(), json=body,
        )
        if r.status_code >= 400:
            raise ProviderError(r.status_code, r.text, self.endpoint.name)
        return r.json()

    async def stream(self, messages: List[Dict[str, Any]], *,
                      model: str,
                      max_tokens: Optional[int] = None,
                      temperature: Optional[float] = None,
                      extra: Optional[Dict[str, Any]] = None,
                      ) -> AsyncIterator[str]:
        body: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if max_tokens is not None:  body["max_tokens"] = max_tokens
        if temperature is not None: body["temperature"] = temperature
        if extra:                   body.update(extra)

        async with self._client.stream(
            "POST", self.endpoint.chat_url,
            headers=self.endpoint.headers(), json=body,
        ) as r:
            if r.status_code >= 400:
                err = await r.aread()
                raise ProviderError(r.status_code, err.decode("utf-8", "replace"),
                                     self.endpoint.name)
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                # OpenAI delta shape — ignore tool_call deltas for the chat loop
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
