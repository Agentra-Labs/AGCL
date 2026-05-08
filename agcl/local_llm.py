"""
Wraps llama-cpp-python. Model is loaded lazily on first use and unloaded
when the system is idle (called from the idle watcher in main.py).

generate_prefix() is the one function the rest of the code calls.
It returns (prefix_string, latency_sec).
"""

import re
import time
import threading
from agcl.config import (
    LOCAL_MODEL_PATH,
    LOCAL_MODEL_TYPE,
    LOCAL_N_CTX,
    LOCAL_N_GPU_LAYERS,
    LOCAL_N_THREADS,
    PREFIX_WORD_COUNT,
)

from agcl.templates import MODEL_TEMPLATES


# Signals that the small local model is regurgitating training-data garbage
# (worksheets, exam answers, structured lists, role labels). When any of
# these match we drop the prefix entirely so the cloud generates cleanly
# from scratch — the whole point of the prefix is "fast first words", not
# "derail the cloud with junk".
_GARBAGE_PATTERNS = [
    r"^#",                       # markdown headings: "####", "##"
    r"\b[A-Z]\s*:",              # "A:", "B :", "Q:" — labeled exam answers
    r"^\s*\d+\s*[.)]\s",         # "1. ", "2) " — numbered list openings
    r"\(\s*[A-Z]\s*\)",          # "(D)", "(A)"
    r"→",                        # arrow used in worksheets
    r"<\|",                      # leaked chat-template tokens
]
_GARBAGE_RE = re.compile("|".join(_GARBAGE_PATTERNS))


def _looks_like_garbage(text: str) -> bool:
    if not text:
        return False
    head = text[:120]
    return bool(_GARBAGE_RE.search(head))

_model = None
_lock  = threading.Lock()


#  model lifecycle 

def _load():
    global _model
    from llama_cpp import Llama
    print("[local_llm] loading model...")
    _model = Llama(
        model_path=LOCAL_MODEL_PATH,
        n_ctx=LOCAL_N_CTX,
        n_gpu_layers=LOCAL_N_GPU_LAYERS,
        n_threads=LOCAL_N_THREADS,
        verbose=False,
    )
    print("[local_llm] model ready")

def _get():
    global _model
    if _model is None:
        _load()
    return _model

def unload():
    global _model
    with _lock:
        if _model is not None:
            print("[local_llm] unloading (idle)")
            _model = None   # GC handles cleanup

#  prefix generation 

def generate_prefix(messages, word_count=None):
    """
    Runs local model to produce the first `word_count` words of a response.
    Returns (prefix: str, latency_sec: float).
    If the model fails for any reason, returns ("", elapsed) so the cloud
    can still take over cleanly.
    """
    word_count = word_count or PREFIX_WORD_COUNT
    template = MODEL_TEMPLATES[LOCAL_MODEL_TYPE]

    prompt = template["format_prompt"](messages)
    gen_cfg = template["generation"]

    t0 = time.time()
    try:
        with _lock:
            m = _get()
            m.reset()
            output = m(
                prompt,
                max_tokens=gen_cfg["max_tokens"],
                temperature=gen_cfg["temperature"],
                top_p=gen_cfg["top_p"],
                top_k=gen_cfg["top_k"],
                repeat_penalty=gen_cfg["repeat_penalty"],
                stop=gen_cfg["stop"],
                echo=False,
                stream=False,
            )
            raw = output["choices"][0]["text"]

        # Truncate at the first newline — the prefix is meant to be the
        # opening of a single sentence, not a multi-line block.
        raw = raw.split("\n", 1)[0]
        # Strip leading quote/punctuation noise.
        raw = re.sub(r"^[\s\"'`:;\-*_>#]+", "", raw).strip()

        if _looks_like_garbage(raw):
            print(f"[local_llm] discarding garbage prefix: {raw!r}")
            prefix = ""
        else:
            words = raw.split()[:word_count]
            prefix = " ".join(words).strip()
            print(f"[local_llm] raw={raw!r}  prefix={prefix!r}")
    except Exception as e:
        print(f"[local_llm] inference error: {e}")
        prefix = ""

    return prefix, time.time() - t0