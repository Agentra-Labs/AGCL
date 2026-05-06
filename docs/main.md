# Agent Codebase Docs

Read this top to bottom once to understand the system, then use the per-file
sections as a reference when you need to find or change something specific.

---

## How the system works in one paragraph

A user message arrives at FastAPI. The local llama.cpp model immediately
generates the first 6 words and streams them back so the user sees something
fast. Then the cloud model (OpenAI or Claude) continues from exactly that
prefix, never restarting. Sessions are kept in memory and flushed to disk when
idle. If a session's messages grow too large, older turns are compressed into a
summary. The system tracks what hours of day you use it, and after a few days
of data it knows your patterns and can trigger callbacks (like pre-warming the
model) before you even open the CLI.

---

## File map

    config.py       all settings, read by every other file
    state.py        in-memory sessions, disk flush, idle detection
    pressure.py     API request rate + latency tracker
    local_llm.py    llama.cpp wrapper, prefix generation, idle unload
    cloud.py        OpenAI + Claude streaming with continuation logic
    context.py      token counting, overflow detection, recontextualization
    patterns.py     usage pattern learning, reactive hour-based triggers
    main.py         FastAPI app, routes, idle watcher, SSE streaming
    cli.py          terminal client for testing
    requirements.txt

---

## config.py

Single place for every tunable. All values read from environment variables with
sensible defaults so you can run without setting anything.

Variables and what they do:

    LOCAL_MODEL_PATH      path to your .gguf file
                          default: models/nano.gguf

    LOCAL_N_CTX           context window size for local model
                          default: 2048

    LOCAL_N_GPU_LAYERS    layers to offload to GPU, 0 = CPU only
                          default: 0

    LOCAL_N_THREADS       CPU threads for local inference
                          default: 4

    PREFIX_WORD_COUNT     how many words the local model generates before
                          handing off to cloud
                          default: 6

    OPENAI_API_KEY        OpenAI key, can be empty if not using OpenAI
    ANTHROPIC_API_KEY     Anthropic key, can be empty if not using Claude

    OPENAI_MODEL          which OpenAI model to call
                          default: gpt-4o

    CLAUDE_MODEL          which Anthropic model to call
                          default: claude-sonnet-4-20250514

    DEFAULT_CLOUD         which cloud to use when the request doesn't specify
                          default: claude

    STATE_DIR             folder where sessions and patterns are saved
                          default: .agent_state
                          created automatically on import

    IDLE_FLUSH_SEC        seconds of inactivity before flushing to disk
                          and unloading the local model
                          default: 180

    SESSION_TTL_SEC       seconds before an idle session is evicted from RAM
                          (it stays on disk, just not in memory)
                          default: 3600

    MAX_CONTEXT_TOKENS    token count that triggers recontextualization
                          default: 6000

    RECONTEX_KEEP_RECENT  how many recent messages to keep verbatim after
                          compressing older ones
                          default: 6

    PRESSURE_WINDOW_SEC   sliding window for request rate measurement
                          default: 60

    PRESSURE_LIMIT        requests per window considered 100% load
                          default: 20

    MIN_PATTERN_DAYS      distinct days needed before an hour is "learned"
                          default: 3

    PATTERN_LOOKBACK_DAYS how far back pattern history is kept
                          default: 30

Nothing in this file does computation. Import it anywhere with no side effects
beyond creating STATE_DIR.

---

## state.py

Owns all session data. Everything else that needs session state goes through
here. Never touches the session dict directly from another file.

A session is a plain dict:
    {
        "messages":    list of {role, content} dicts,
        "created_at":  unix timestamp,
        "last_active": unix timestamp
    }

Sessions live in the _sessions dict in memory. When idle or when TTL expires,
they get written to STATE_DIR/sess_{id}.json and removed from memory. Next
access reloads from disk transparently.

Functions:

    touch()
        Call on any user activity. Updates the global last-active time
        used by is_idle().

    idle_seconds() -> float
        Seconds since the last touch(). Used by the idle watcher in main.py.

    is_idle() -> bool
        True if idle_seconds() > IDLE_FLUSH_SEC.

    get_session(sid) -> dict
        Returns the session for sid. If not in memory, loads from disk.
        If not on disk either, creates a fresh session. Never returns None.

    put_session(sid, sess)
        Saves sess back into memory and updates last_active on it.
        Does not write to disk — use flush_session for that.

    flush_session(sid)
        Writes a single session to disk atomically (write tmp, os.replace).

    flush_all()
        Writes every live session to disk. Called by the idle watcher.

    evict_idle_sessions() -> int
        Flushes then removes sessions older than SESSION_TTL_SEC from memory.
        Returns how many were evicted. Disk copies remain.

    session_list() -> list
        Names of sessions currently in memory.

Disk writes use a tmp-then-replace pattern so a crash mid-write never
corrupts an existing file.

---

## pressure.py

Measures how hard you're hitting the cloud API. Two signals combined:
request rate in a sliding window, and a rolling average of response latency.

The rate_ratio is requests_in_window / PRESSURE_LIMIT. A ratio above 0.8
sets the high flag. Latency is just informational — useful for noticing when
a provider is slow even if rate is fine.

Functions:

    record(latency_sec)
        Call after every cloud response finishes. Appends a timestamp to
        the sliding window and the latency to the rolling buffer (last 50).

    pressure() -> dict
        Returns:
            requests_in_window  how many requests in the last PRESSURE_WINDOW_SEC
            rate_ratio          requests / PRESSURE_LIMIT, capped at 2.0
            avg_latency_sec     mean of last 50 latencies
            high                bool, true if rate_ratio > 0.8

    is_high() -> bool
        Shortcut for pressure()["high"]. Use this for conditional logic.

No locking here because deque appends and reads from a single async loop
are safe enough for a monitoring tool. Not suitable as a strict rate limiter.

---

## local_llm.py

Wraps llama-cpp-python. The model is loaded lazily — nothing happens until
the first inference call. When unload() is called (by the idle watcher in
main.py), the reference is dropped and the GC reclaims the memory.

The only function main.py and tests need to call is generate_prefix().

Functions:

    generate_prefix(messages, word_count=None) -> (str, float)
        Takes the current session messages list and generates the first
        word_count words of the assistant's response using the local model.
        Returns a tuple of (prefix_string, latency_in_seconds).

        If the model fails for any reason (file missing, OOM, bad output),
        returns ("", elapsed) rather than raising. This means the cloud call
        still happens, just without a prefix — the system degrades gracefully.

        word_count defaults to PREFIX_WORD_COUNT from config.

    unload()
        Sets the global model reference to None. Called by the idle watcher.
        The model reloads automatically on the next generate_prefix() call.

Internal helpers (not called from outside):

    _load()
        Instantiates the Llama object with config settings. Prints a
        loading message because this takes a few seconds.

    _get()
        Returns the loaded model, calling _load() if needed.

    _to_chatml(messages)
        Converts message list to ChatML format. Works for most instruction-
        tuned GGUFs. If your model uses a different template, edit this
        function — it's the only formatting code in the file.

The _lock wraps _get() and inference together so two concurrent requests
don't try to load the model simultaneously. Inference itself is synchronous
and blocking — FastAPI runs it in a thread automatically via its async
streaming response handling.

---

## cloud.py

Handles streaming from OpenAI and Claude. The key idea from the paper is that
the cloud model receives the local prefix as an already-open assistant turn,
so it continues rather than restarts. Both APIs support this natively.

For OpenAI: the messages list ends with {"role": "assistant", "content": prefix}
and the model fills in the rest.

For Claude: same pattern. The Anthropic API treats a trailing assistant message
as a prefill and generates from that point.

Recovery modes (injected into the system prompt):

    natural     cloud pivots to the correct answer without mentioning the prefix
                mismatch. This is the default and what users preferred in the paper.

    humor       cloud can warmly and briefly acknowledge the mismatch before
                correcting. Good for casual contexts.

    explicit    cloud directly says the prefix was wrong, then answers correctly.
                Bluntest option, lowest user preference in the paper's study.

Functions:

    stream_continuation(history, prefix, provider=None, recovery_mode="natural")
        Async generator. Yields text chunks that follow the prefix.
        Calls pressure.record() with the total latency when the stream ends.
        provider defaults to DEFAULT_CLOUD if not specified.

    one_shot(history, provider=None) -> str
        Non-streaming. Collects the full response and returns it as a string.
        Used by context.py to summarize old messages, and useful for testing.

Internal helpers:

    _build_system(recovery_mode) -> str
        Assembles the system prompt from the base continuation instruction
        and the selected recovery mode note.

    _build_messages(history, prefix, recovery_mode) -> list
        Strips existing system messages from history, adds the new system
        prompt, and appends the prefix as the open assistant turn.

    _stream_openai(msgs)
        Async generator over the OpenAI streaming chat completion.

    _stream_claude(msgs, prefix)
        Async generator over the Anthropic streaming messages API.
        Separates system content from turns before calling the API because
        Anthropic takes system as a separate parameter.

---

## context.py

Keeps sessions alive indefinitely by compressing old messages when the context
window fills. Does not lose information — it summarizes rather than truncates.

The compression strategy:
    1. Identify all messages over the "recent" threshold.
    2. Ask the cloud to summarize them into a short paragraph.
    3. Inject that paragraph as a system message.
    4. Keep the last RECONTEX_KEEP_RECENT messages verbatim.

If the cloud summary call fails, falls back to a naive 80-char-per-message
truncation so the session can continue regardless.

Token counting uses tiktoken if installed (real cl100k counts), otherwise
estimates at 4 characters per token. Install tiktoken for accuracy.

Functions:

    count_tokens(text) -> int
        Counts tokens in a string. Switches implementation based on whether
        tiktoken imported successfully.

    messages_tokens(messages) -> int
        Total token count across all message content in a list.

    needs_recontex(messages) -> bool
        True if messages_tokens exceeds MAX_CONTEXT_TOKENS.

    recontextualize(messages) -> list   (async)
        Returns a new messages list within the token budget. If no overflow,
        returns the input unchanged. Prints a log line when compression runs.

    append(messages, role, content) -> list   (async)
        The function main.py calls. Appends a new message and runs
        recontextualize() in one step. Returns the new list.

Internal helpers:

    summarize_older(messages) -> str   (async)
        Tries cloud summarization first, falls back to _summarize_naive.

    _summarize_via_cloud(messages) -> str   (async)
        Calls one_shot() in cloud.py with a summarization prompt.

    _summarize_naive(messages) -> str
        Truncates each message to 80 chars and joins them. Never fails.

---

## patterns.py

Learns which hours of day you use the agent. After MIN_PATTERN_DAYS distinct
calendar days of usage at a given hour, that hour becomes "active". Active
hours are stored as a list of timestamps per hour in patterns.json.

The scheduler ticks every 60 seconds. If the current hour is active, all
registered callbacks fire. You register callbacks in main.py's startup handler.
The default callback just logs. Replace or add to it for things like pre-loading
the model, sending a daily summary, or triggering a sync.

Patterns survive restarts because everything writes to disk immediately on
each record_usage() call.

Functions:

    load()
        Reads patterns.json from STATE_DIR into memory. Call once at startup.

    save()
        Writes current patterns to disk atomically. Called internally by
        record_usage(). You rarely need to call this directly.

    record_usage()
        Call once per user request. Records the current hour's timestamp,
        prunes anything older than PATTERN_LOOKBACK_DAYS, and saves.

    active_hours() -> dict
        Returns {hour: hit_count} for hours that appear on at least
        MIN_PATTERN_DAYS distinct calendar days. Hours below the threshold
        are excluded — they're noise.

    upcoming_active_hours(lookahead_hours=3) -> list
        Returns active hours in the next N hours. Useful for pre-warming.

    register_trigger(fn)
        Registers fn(hour, hit_count) to be called when the current hour
        is active. Call this in main.py's startup. Multiple callbacks ok.

    start_scheduler()
        Starts the background thread. Call once at startup. The thread is
        daemon so it dies with the process.

Data format on disk (patterns.json):
    {
        "9":  [1716000000.0, 1716086400.0, ...],
        "14": [1716018000.0, ...]
    }
Keys are hours as strings (JSON requirement). Values are lists of unix
timestamps within the lookback window.

---

## main.py

The FastAPI app. Ties everything together. Contains the routes, the idle
watcher thread, and the SSE streaming logic.

On startup: loads patterns from disk, starts the pattern scheduler, registers
a default trigger that logs active hours, and starts the idle watcher thread.

The idle watcher runs every 30 seconds. When is_idle() is true it calls
flush_all(), evict_idle_sessions(), and unload_local_model().

All chat responses are Server-Sent Events. Each SSE line is a JSON object
with a "type" field:

    {"type": "prefix", "text": "...", "local_ms": 42}
        First message. Arrives almost immediately. Contains the local model's
        output and how long it took in milliseconds.

    {"type": "chunk", "text": "..."}
        One chunk from the cloud stream. Many of these follow the prefix.

    {"type": "done", "pressure": {...}}
        End of response. Contains the current pressure snapshot.

Routes:

    POST /chat/{session_id}
        Body: {"message": "...", "provider": "claude"|"openai", "recovery_mode": "natural"|"humor"|"explicit"}
        provider and recovery_mode are optional.
        Returns SSE stream.

    GET /session/{session_id}
        Returns the full session: message list, message count, timestamps.
        Useful for debugging conversation state.

    DELETE /session/{session_id}
        Flushes the session to disk. Does not delete the disk file.
        Use this to force a save without waiting for idle flush.

    GET /pressure
        Returns the current pressure dict from pressure.py.

    GET /patterns
        Returns active_hours and upcoming_active_hours.

    GET /health
        Returns idle_sec, is_idle, sessions_live count, and pressure.
        Good first check if something seems slow or broken.

Internal:

    _run_chat(session_id, user_message, provider, recovery_mode)
        Async generator that does the actual work: touch state, record
        pattern, append message, get prefix, stream continuation, save
        response. Extracted from the route so it's reusable.

    _sse(data) -> str
        Formats a string as an SSE data line. One-liner kept as a function
        so the format is consistent everywhere.

    _idle_watcher()
        Blocking loop in a daemon thread. Sleeps 30s, checks is_idle(),
        acts if true.

---

## cli.py

Terminal client. Connects to the running FastAPI server and streams responses
to stdout. Useful for development and daily use without building a UI.

Usage:
    python cli.py
    python cli.py --session work
    python cli.py --provider openai
    python cli.py --recovery humor

Arguments:
    --session     session name (default: "default")
    --provider    force openai or claude, skips default
    --recovery    natural | humor | explicit (default: natural)

Output format:
    The prefix token appears in grey with its local latency in brackets.
    Cloud chunks print immediately after with no separator.
    At the end of each response, pressure stats print in grey.

The client uses httpx's streaming context manager. Each SSE line is parsed
as JSON and dispatched by type field. If the server isn't running you get a
connection error immediately.

---

## requirements.txt

    fastapi             web framework
    uvicorn[standard]   ASGI server with websocket support
    httpx               used by cli.py for SSE streaming
    llama-cpp-python    local model inference
    openai              OpenAI SDK
    anthropic           Anthropic SDK
    tiktoken            accurate token counting (optional but recommended)

To install:
    pip install -r requirements.txt

For llama-cpp-python with GPU support, install separately:
    CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python

To run:
    uvicorn main:app --port 8000 --reload