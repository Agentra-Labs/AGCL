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

    Closes the underlying httpx pool before returning so we don't get
    "Event loop is closed" tracebacks on CLI exit.
    """
    from agcl.toolkit.registry import get_chat_client
    client = get_chat_client()
    if client is None:
        return  # caller will fall back to provider-direct path
    model = getattr(client, "default_model", None) or "smart"
    try:
        async for chunk in client.stream(msgs, model=model):
            yield chunk
    finally:
        try: await client.aclose()
        except Exception: pass


async def _stream_openai(msgs):
    """
    Stream via the OpenAI SDK. The SDK's AsyncOpenAI wraps an httpx
    AsyncClient; we close it explicitly here. If we don't, GC closes
    it after asyncio.run() has already shut the loop down — which
    yields the "Event loop is closed" tracebacks.
    """
    import openai
    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    try:
        async with client.chat.completions.stream(
            model=OPENAI_MODEL,
            messages=msgs,
        ) as stream:
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield delta
    finally:
        try: await client.close()
        except Exception: pass


async def _stream_claude(msgs, prefix):
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    turns  = [m for m in msgs if m["role"] != "system"]

    # Anthropic requires last turn to be user unless using prefill.
    # We keep the trailing assistant message — Claude continues from it.
    try:
        async with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            messages=turns,
        ) as stream:
            async for text in stream.text_stream:
                yield text
    finally:
        # Close the SDK's owned httpx pool before the asyncio loop tears
        # down (otherwise httpx's GC-time aclose hits a closed loop).
        try: await client.close()
        except Exception: pass


#  public entry point

async def stream_continuation(history, prefix, provider=None, recovery_mode="natural",
                                session_id=None, kind="chat"):
    """
    Async generator. Yields text chunks that follow the prefix.

    Records:
      - latency into the pressure tracker (always)
      - tokens/cost into agcl.usage (per-call), tagged with `kind` so
        the dashboard can split chat vs knowledge-formation vs other.
        `kind` ∈ {"chat", "knowledge", "summarize", "other"}.
      - quota check fires before the call; QuotaExceeded propagates.
    """
    from agcl import usage
    provider = provider or DEFAULT_CLOUD
    msgs = _build_messages(history, prefix, recovery_mode)

    # Pre-flight quota check (cheap; raises QuotaExceeded on cap).
    usage.check_quota(provider)

    # If a toolkit gateway is configured (LiteLLM proxy, vLLM, or Ollama),
    # route through it — it handles fallback / retry / spend tracking.
    # Falls through to the provider-direct path if no gateway env is set.
    import os as _os
    use_gateway = any(_os.getenv(k) for k in
                       ("AGCL_LLM_BASE_URL", "VLLM_HOST", "OLLAMA_HOST"))
    effective_provider = "litellm" if use_gateway else provider
    effective_model = (
        _os.getenv("AGCL_LLM_MODEL", "smart") if use_gateway
        else (OPENAI_MODEL if provider == "openai" else CLAUDE_MODEL)
    )

    in_text = "\n".join(m.get("content","") for m in msgs)
    out_chunks: list = []
    t0 = time.time()
    err = None
    try:
        if use_gateway:
            gen = _stream_toolkit_gateway(msgs)
        elif provider == "openai":
            gen = _stream_openai(msgs)
        else:
            gen = _stream_claude(msgs, prefix)

        async for chunk in gen:
            out_chunks.append(chunk)
            yield chunk
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        raise
    finally:
        elapsed = time.time() - t0
        prs.record(elapsed)
        try:
            usage.record_call(
                provider=effective_provider, model=effective_model,
                kind=kind, session_id=session_id,
                in_text=in_text, out_text="".join(out_chunks),
                latency_sec=elapsed, ok=err is None, error=err,
            )
        except Exception:
            # Never let bookkeeping kill a call.
            pass


async def one_shot(history, provider=None, session_id=None, kind="chat"):
    """
    Plain cloud call with no prefix — used for fallback or non-streaming needs.
    Returns full text string.
    """
    provider = provider or DEFAULT_CLOUD
    parts = []
    async for chunk in stream_continuation(history, prefix="", provider=provider,
                                             recovery_mode="natural",
                                             session_id=session_id, kind=kind):
        parts.append(chunk)
    return "".join(parts)
