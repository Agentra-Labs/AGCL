"""
Simple stable tokenizer for the mini model.

We don't want to depend on a specific HF tokenizer (which would couple
the mini model's vocab to whatever planner agent the user happens to
have configured). Instead we use a fixed-size byte-pair-ish tokenizer
backed by tiktoken when available, falling back to a deterministic
hash-trick tokenizer that's compatible with any vocab size.

API:
    encode(text, max_len=...)  -> list[int]
    decode(ids)                -> str
    vocab_size()               -> int
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

_BACKEND = None
_VOCAB = 8192


def init(vocab_size: int = 8192) -> None:
    global _BACKEND, _VOCAB
    _VOCAB = vocab_size
    try:
        import tiktoken                      # type: ignore
        _BACKEND = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _BACKEND = None


def vocab_size() -> int:
    return _VOCAB


def encode(text: str, max_len: Optional[int] = None) -> List[int]:
    if _BACKEND is None:
        init(_VOCAB)
    if _BACKEND is not None:
        toks = _BACKEND.encode(text or "")
        # Bucket into our smaller vocab so embedding tables stay tiny.
        toks = [t % _VOCAB for t in toks]
    else:
        # Deterministic hash trick on whitespace-split tokens.
        toks = []
        for w in (text or "").split():
            h = int(hashlib.md5(w.encode()).hexdigest()[:8], 16)
            toks.append(h % _VOCAB)
    if max_len is not None:
        toks = toks[:max_len]
    return toks


def decode(ids: List[int]) -> str:
    if _BACKEND is None:
        init(_VOCAB)
    if _BACKEND is not None:
        # Reverse-bucketing is lossy; we approximate by using the bucket
        # index as a cl100k id. Not pretty, but only used for /mini test
        # sanity checks.
        try:
            return _BACKEND.decode([int(i) for i in ids])
        except Exception:
            pass
    return " ".join(f"<{i}>" for i in ids)
