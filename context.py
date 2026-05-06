"""
Context window management.

When a session's messages exceed MAX_CONTEXT_TOKENS:
  1. Keep system messages intact.
  2. Compress older turns into a summary injected as a system note.
  3. Keep the last RECONTEX_KEEP_RECENT turns verbatim.

summarize_older() calls the cloud by default but falls back to naive
truncation if no API keys are set — so the thing never crashes.
"""

import asyncio
from config import MAX_CONTEXT_TOKENS, RECONTEX_KEEP_RECENT

# token counting — use tiktoken if available, else rough char estimate
try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text):
        return len(_enc.encode(text))
except ImportError:
    def count_tokens(text):
        return len(text) // 4   # rough estimate: ~4 chars per token


def messages_tokens(messages):
    return sum(count_tokens(m.get("content", "")) for m in messages)

def needs_recontex(messages):
    return messages_tokens(messages) > MAX_CONTEXT_TOKENS


# ── summarizer ────────────────────────────────────────────────────────────────

async def _summarize_via_cloud(messages):
    """Ask cloud to compress older turns into a short summary."""
    import cloud as cl
    combined = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
    prompt = [
        {"role": "system", "content": "Summarize the following conversation history concisely in 3-5 sentences. Preserve key facts, decisions, and context."},
        {"role": "user",   "content": combined},
    ]
    return await cl.one_shot(prompt)

def _summarize_naive(messages):
    """Fallback: just truncate each message to 80 chars."""
    lines = []
    for m in messages:
        snippet = m["content"][:80].replace("\n", " ")
        lines.append(f"{m['role']}: {snippet}…")
    return "[Earlier conversation: " + " | ".join(lines) + "]"

async def summarize_older(messages):
    try:
        return await _summarize_via_cloud(messages)
    except Exception as e:
        print(f"[context] cloud summarize failed ({e}), using naive truncation")
        return _summarize_naive(messages)


# ── recontextualization ───────────────────────────────────────────────────────

async def recontextualize(messages):
    """
    Returns a new messages list that fits within token budget.
    If no overflow, returns messages unchanged.
    """
    if not needs_recontex(messages):
        return messages

    system_msgs = [m for m in messages if m["role"] == "system"]
    turns       = [m for m in messages if m["role"] != "system"]

    if len(turns) <= RECONTEX_KEEP_RECENT:
        return messages

    older  = turns[:-RECONTEX_KEEP_RECENT]
    recent = turns[-RECONTEX_KEEP_RECENT:]

    summary_text = await summarize_older(older)
    summary_msg  = {"role": "system", "content": f"[Context summary] {summary_text}"}

    result = system_msgs + [summary_msg] + recent
    print(f"[context] recontextualized: {len(older)} turns → 1 summary, kept {len(recent)} recent")
    return result


# ── append helper used by main ────────────────────────────────────────────────

async def append(messages, role, content):
    """Append a message and recontextualize if needed. Returns new list."""
    messages = list(messages)
    messages.append({"role": role, "content": content})
    return await recontextualize(messages)
