import json
import os

# Load .env into os.environ before anything else reads getenv.
import agcl.secrets as _secrets  # noqa: F401

# Project-local HuggingFace cache. Overridable via HF_HOME / HF_HUB_CACHE
# env vars; default keeps every downloaded model under ./models/hf/ so
# the project is self-contained and easy to clean up.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("HF_HOME", os.path.join(_PROJECT_ROOT, "models", "hf"))

# local model
LOCAL_MODEL_PATH   = os.getenv("LOCAL_MODEL_PATH", "models/SmolLM2-135M.Q2_K.gguf")
# local model type
LOCAL_MODEL_TYPE = os.getenv("LOCAL_MODEL_TYPE", "smollm2")
LOCAL_N_CTX        = int(os.getenv("LOCAL_N_CTX", "2048"))
LOCAL_N_GPU_LAYERS = int(os.getenv("LOCAL_N_GPU_LAYERS", "0"))
LOCAL_N_THREADS    = int(os.getenv("LOCAL_N_THREADS", "12"))
PREFIX_WORD_COUNT  = int(os.getenv("PREFIX_WORD_COUNT", "4"))  # words local model fires before cloud takes over

# cloud — API keys live in agcl/secrets.py (loaded from .env / env vars)
from agcl.secrets import OPENAI_API_KEY, ANTHROPIC_API_KEY  # noqa: E402,F401
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o")
CLAUDE_MODEL    = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
DEFAULT_CLOUD   = os.getenv("DEFAULT_CLOUD", "claude")   # "openai" | "claude"

# persistence
STATE_DIR          = os.getenv("STATE_DIR", ".agent_state")
IDLE_FLUSH_SEC     = int(os.getenv("IDLE_FLUSH_SEC", "180"))   # flush + unload local model after N seconds quiet
SESSION_TTL_SEC    = int(os.getenv("SESSION_TTL_SEC", "3600"))  # evict session from memory after 1h

# context management
MAX_CONTEXT_TOKENS    = int(os.getenv("MAX_CONTEXT_TOKENS", "6000"))
RECONTEX_KEEP_RECENT  = int(os.getenv("RECONTEX_KEEP_RECENT", "6"))   # messages kept verbatim after compression

# api pressure
PRESSURE_WINDOW_SEC = int(os.getenv("PRESSURE_WINDOW_SEC", "60"))
PRESSURE_LIMIT      = int(os.getenv("PRESSURE_LIMIT", "20"))   # requests per window considered max

# patterns
MIN_PATTERN_DAYS = int(os.getenv("MIN_PATTERN_DAYS", "3"))   # need this many distinct days before hour is "learned"
PATTERN_LOOKBACK_DAYS = int(os.getenv("PATTERN_LOOKBACK_DAYS", "30"))

os.makedirs(STATE_DIR, exist_ok=True)


# -------------------- recursive MAS --------------------
#
# Each agent is described by a small dict:
#   {"backend": "hf"   | "gguf",
#    "model":   "<HF id or local path or gguf path>",
#    "role":    "planner" | "critic" | "solver" | ...,
#    # hf-only:
#    "dtype":   "float32" | "float16" | "bfloat16",
#    "device":  "cpu" | "cuda",
#    # gguf-only:
#    "n_ctx":         2048,
#    "n_gpu_layers":  0,
#    "n_threads":     4,
#   }
#
# Three ways to override (checked in order):
#   1. MAS_AGENTS env var holding a JSON array
#   2. MAS_CONFIG_FILE env var pointing to a .json file with key "agents"
#   3. the python default below
_MAS_DEFAULT_AGENTS = [
    {"backend": "gguf", "model": LOCAL_MODEL_PATH, "role": "planner"},
    {"backend": "gguf", "model": LOCAL_MODEL_PATH, "role": "solver"},
]


def _load_mas_agents():
    raw = os.getenv("MAS_AGENTS", "").strip()
    if raw:
        return json.loads(raw)
    cfg_file = os.getenv("MAS_CONFIG_FILE", "").strip()
    if cfg_file and os.path.exists(cfg_file):
        with open(cfg_file) as f:
            return json.load(f).get("agents", _MAS_DEFAULT_AGENTS)
    return _MAS_DEFAULT_AGENTS


MAS_AGENTS  = _load_mas_agents()
MAS_PATTERN = os.getenv("MAS_PATTERN", "sequential")     # sequential|moe|distill|deliberation|custom
MAS_ROUNDS  = int(os.getenv("MAS_ROUNDS", "2"))
MAS_DEVICE  = os.getenv("MAS_DEVICE", "cpu")             # default device for hf agents that don't set their own
MAS_DTYPE   = os.getenv("MAS_DTYPE",  "float32")         # default dtype for hf agents


# -------------------- mini-model background trainer --------------------
#
# Optional. When MINI_ENABLED=1, a tiny pluggable model trains in the
# background off latents + reasoning trajectories captured during normal
# AGCL use. Designed to be cheap on CPU (low priority, sleep between
# steps) and pause/resume cleanly. See docs/minimodel.md.

MINI_ENABLED      = os.getenv("MINI_ENABLED", "0").strip() in ("1", "true", "True", "yes", "on")
MINI_AUTOSTART    = os.getenv("MINI_AUTOSTART", "1").strip() in ("1", "true", "True", "yes", "on")
MINI_ARCH         = os.getenv("MINI_ARCH", "transformer")    # "transformer" | "mlp"
MINI_HIDDEN       = int(os.getenv("MINI_HIDDEN", "128"))
MINI_LAYERS       = int(os.getenv("MINI_LAYERS", "2"))
MINI_HEADS        = int(os.getenv("MINI_HEADS", "4"))
MINI_MAX_SEQ      = int(os.getenv("MINI_MAX_SEQ", "256"))
MINI_VOCAB        = int(os.getenv("MINI_VOCAB", "8192"))
MINI_ATTENTION    = os.getenv("MINI_ATTENTION", "causal")    # causal|full|sliding|banded|dilated
MINI_WINDOW       = int(os.getenv("MINI_WINDOW", "64"))      # used by sliding/banded/dilated
MINI_STRATEGY     = os.getenv("MINI_STRATEGY", "ce_plus_latent")  # see agcl/mini/strategies.py
MINI_LR           = float(os.getenv("MINI_LR", "3e-4"))
MINI_BUFFER_SIZE  = int(os.getenv("MINI_BUFFER_SIZE", "1024"))
MINI_BATCH_SIZE   = int(os.getenv("MINI_BATCH_SIZE", "2"))
MINI_YIELD_MS     = int(os.getenv("MINI_YIELD_MS", "200"))   # sleep between steps so main work has CPU
MINI_NICE         = int(os.getenv("MINI_NICE", "15"))        # POSIX niceness (higher = lower priority)
MINI_CKPT_INTERVAL = int(os.getenv("MINI_CKPT_INTERVAL", "50"))
MINI_STATE_DIR    = os.getenv("MINI_STATE_DIR", os.path.join(STATE_DIR, "mini"))
os.makedirs(MINI_STATE_DIR, exist_ok=True)
