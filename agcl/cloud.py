"""
Cloud streaming for OpenAI and Claude.

The key contract (from the paper):
  - Cloud model is given the local prefix as an ALREADY-STARTED assistant turn.
  - It must CONTINUE, not restart.
  - Three recovery modes when prefix might be off: natural, humor, explicit.

Both providers support prefilling the assistant turn (OpenAI via trailing
assistant message, Claude via the same pattern). We use that instead of
injecting the prefix into the system prompt, because it actually controls
the generation start point rather than just hinting at it.
"""

import time
import asyncio
import agcl.pressure as prs
from agcl.config import (
    OPENAI_API_KEY, ANTHROPIC_API_KEY,
    OPENAI_MODEL, CLAUDE_MODEL, DEFAULT_CLOUD,
)

#  continuation system instructions 

_BASE = (
    "You are a helpful assistant. A prefix of your response has already been "
    "shown to the user. Continue seamlessly from where it ends. "
    "Never repeat the prefix. Never reference it. Just keep going."
)

_RECOVERY = {
    "natural":  "If the prefix doesn't align with the right answer, smoothly pivot without drawing attention to it.",
    "humor":    "If the prefix is off, a brief warm acknowledgment before correcting is fine. Keep it light.",
    "explicit": "If the prefix is wrong, say so clearly and concisely, then give the correct answer.",
}


def _build_system(recovery_mode):
    note = _RECOVERY.get(recovery_mode, _RECOVERY["natural"])
    return f"{_BASE} {note}"


def _build_messages(history, prefix, recovery_mode):
    """
    Injects prefix as an open assistant turn. Both OpenAI and Claude
    will continue generating from that point.
    """
    sys_content = _build_system(recovery_mode)
    msgs = [{"role": "system", "content": sys_content}]
    msgs += [m for m in history if m["role"] != "system"]

    if prefix:
        # open assistant turn = model continues from here
        msgs.append({"role": "assistant", "content": prefix})
    return msgs


#  streaming generators

async def _stream_toolkit_gateway(msgs):
    """
    Route through agcl.toolkit when AGCL_LLM_BASE_URL (LiteLLM proxy),
    VLLM_HOST, or OLLAMA_HOST is set. The gateway client picks the
    right backend automatically; we just need to pass messages.
    """
    from agcl.toolkit.registry import get_chat_client
    client = get_chat_client()
    if client is None:
        return  # caller will fall back to provider-direct path
    model = getattr(client, "default_model", None) or "smart"
    async for chunk in client.stream(msgs, model=model):
        yield chunk


async def _stream_openai(msgs):
    import openai
    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    async with client.chat.completions.stream(
        model=OPENAI_MODEL,
        messages=msgs,
    ) as stream:
        async for event in stream:
            delta = event.choices[0].delta.content if event.choices else None
            if delta:
                yield delta


async def _stream_claude(msgs, prefix):
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    turns  = [m for m in msgs if m["role"] != "system"]

    # Anthropic requires last turn to be user unless using prefill.
    # We keep the trailing assistant message — Claude continues from it.
    async with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system,
        messages=turns,
    ) as stream:
        async for text in stream.text_stream:
            yield text


#  public entry point 

async def stream_continuation(history, prefix, provider=None, recovery_mode="natural"):
    """
    Async generator. Yields text chunks that follow the prefix.
    Records latency into the pressure tracker when done.
    """
    provider = provider or DEFAULT_CLOUD
    msgs = _build_messages(history, prefix, recovery_mode)

    # If a toolkit gateway is configured (LiteLLM proxy, vLLM, or Ollama),
    # route through it — it handles fallback / retry / spend tracking.
    # Falls through to the provider-direct path if no gateway env is set.
    import os as _os
    use_gateway = any(_os.getenv(k) for k in
                       ("AGCL_LLM_BASE_URL", "VLLM_HOST", "OLLAMA_HOST"))

    t0 = time.time()
    try:
        if use_gateway:
            gen = _stream_toolkit_gateway(msgs)
        elif provider == "openai":
            gen = _stream_openai(msgs)
        else:
            gen = _stream_claude(msgs, prefix)

        async for chunk in gen:
            yield chunk
    finally:
        prs.record(time.time() - t0)


async def one_shot(history, provider=None):
    """
    Plain cloud call with no prefix — used for fallback or non-streaming needs.
    Returns full text string.
    """
    provider = provider or DEFAULT_CLOUD
    msgs = [m for m in history]
    parts = []
    async for chunk in stream_continuation(history, prefix="", provider=provider, recovery_mode="natural"):
        parts.append(chunk)
    return "".join(parts)
