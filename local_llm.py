"""
Wraps llama-cpp-python. Model is loaded lazily on first use and unloaded
when the system is idle (called from the idle watcher in main.py).

generate_prefix() is the one function the rest of the code calls.
It returns (prefix_string, latency_sec).
"""

import time
import threading
from config import (
    LOCAL_MODEL_PATH, LOCAL_N_CTX, LOCAL_N_GPU_LAYERS,
    LOCAL_N_THREADS, PREFIX_WORD_COUNT
)

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


#  prompt formatting 

def _to_chatml(messages):
    parts = []
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"<|im_start|>system\n{content}<|im_end|>")
        elif role == "user":
            parts.append(f"<|im_start|>user\n{content}<|im_end|>")
        elif role == "assistant":
            parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)

def _to_plain(messages):
    """
    Fallback for base models that aren't instruction-tuned.
    Just puts the last user message as a plain continuation prompt.
    """
    user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
    last = user_msgs[-1] if user_msgs else ""
    return f"Q: {last}\nA:"

# set PROMPT_FORMAT=plain in env if your model isn't ChatML-tuned
import os as _os
_PROMPT_FORMAT = _os.getenv("PROMPT_FORMAT", "chatml")   # "chatml" | "plain"

def _format_prompt(messages):
    if _PROMPT_FORMAT == "plain":
        return _to_plain(messages)
    return _to_chatml(messages)


#  prefix generation 

def generate_prefix(messages, word_count=None):
    """
    Runs local model to produce the first `word_count` words of a response.
    Returns (prefix: str, latency_sec: float).
    If the model fails for any reason, returns ("", elapsed) so the cloud
    can still take over cleanly.
    """
    word_count = word_count or PREFIX_WORD_COUNT
    prompt = _format_prompt(messages)

    t0 = time.time()
    try:
        with _lock:
            m = _get()
            output = m(
                prompt,
                max_tokens=word_count * 3,   # tight ceiling
                temperature=0.7,
                repeat_penalty=1.15,          # prevents "heiz heiz heiz" loops
                stop=["<|im_end|>", "<|im_start|>", "\n\n", "User:", "user:"],
                echo=False,
                stream=False,
            )
        raw = output["choices"][0]["text"]
        # hard cap at word_count words regardless of what the model output
        words = raw.split()[:word_count]
        prefix = " ".join(words).strip()
        print(f"[local_llm] raw={raw!r}  prefix={prefix!r}")
    except Exception as e:
        print(f"[local_llm] inference error: {e}")
        prefix = ""

    return prefix, time.time() - t0