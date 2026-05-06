import os

# local model
LOCAL_MODEL_PATH   = os.getenv("LOCAL_MODEL_PATH", "models/qwen2.5-0.5b-instruct-q5_0.gguf")
# local model type
LOCAL_MODEL_TYPE = os.getenv("LOCAL_MODEL_TYPE", "qwen25")
LOCAL_N_CTX        = int(os.getenv("LOCAL_N_CTX", "2048"))
LOCAL_N_GPU_LAYERS = int(os.getenv("LOCAL_N_GPU_LAYERS", "0"))
LOCAL_N_THREADS    = int(os.getenv("LOCAL_N_THREADS", "12"))
PREFIX_WORD_COUNT  = int(os.getenv("PREFIX_WORD_COUNT", "4"))  # words local model fires before cloud takes over

# cloud
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "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")
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
