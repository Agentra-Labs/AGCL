"""
Wraps llama-cpp-python. Model is loaded lazily on first use and unloaded
when the system is idle (called from the idle watcher in main.py).

generate_prefix() is the one function the rest of the code calls.
It returns (prefix_string, latency_sec).
"""

import time
import threading
from openslock.config import (
    LOCAL_MODEL_PATH,
    LOCAL_MODEL_TYPE,
    LOCAL_N_CTX,
    LOCAL_N_GPU_LAYERS,
    LOCAL_N_THREADS,
    PREFIX_WORD_COUNT,
)

from openslock.templates import MODEL_TEMPLATES

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
            raw = output["choices"][0]["text"].strip()
            # hard remove leading pseudo-search garbage
            for token in ["?", "\n"]:
                if token in raw[:80]:
                    left, right = raw.split(token, 1)

                    # if left side looks like junk query continuation
                    if len(left.split()) <= 12:
                        raw = right.strip()
            # remove weird comma-fragments / title-like prefixes
            if "," in raw[:80]:
                first, rest = raw.split(",", 1)

                # if first chunk looks like poetic fluff, drop it
                if len(first.split()) <= 8:
                    raw = rest.strip()

            # remove leading quote fragments
            raw = raw.lstrip('"\':;- ')
            # remove weird leading pseudo-questions
            bad_prefixes = [
                "what does",
                "what are",
                "why does",
                "who is",
                "how does",
            ]

            lower = raw.lower()

            for bp in bad_prefixes:
                if lower.startswith(bp):
                    splitters = ["\n\n", "\n", ". ", "?"]

                    cut = -1
                    for s in splitters:
                        idx = raw.find(s)
                        if idx != -1:
                            cut = idx + len(s)
                            break

                    if cut != -1:
                        raw = raw[cut:].strip()

                    break
        # hard cap at word_count words regardless of what the model output
        import re
        raw = re.sub(r"^[^\w]+", "", raw).strip()
        words = raw.split()[:word_count]
        prefix = " ".join(words).strip()
        print(f"[local_llm] raw={raw!r}  prefix={prefix!r}")
    except Exception as e:
        print(f"[local_llm] inference error: {e}")
        prefix = ""

    return prefix, time.time() - t0