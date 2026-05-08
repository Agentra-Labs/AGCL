# agcl

> **Documentation map**
>
> | If you want to... | Read this |
> |---|---|
> | Set up the project for the first time (no Python experience needed) | **[docs/guide.md](docs/guide.md)** — 15-min beginner walkthrough |
> | Understand every config knob in plain English | **[docs/configuration.md](docs/configuration.md)** — friendly reference |
> | Set up the recursive multi-agent feature with real models | **[docs/advanced_guide.md](docs/advanced_guide.md)** — picking models step by step |
> | Understand what auto-training is doing under the hood | **[docs/training.md](docs/training.md)** — the `[stage A]` / `[stage B]` lines explained |
> | **Integrate this PC into a GUI / web frontend** | **[docs/integration.md](docs/integration.md)** — node API, auth, SSE events, every editable config |
> | Get a terse technical reference for the multi-agent feature | **[docs/recursive_mas_setup.md](docs/recursive_mas_setup.md)** |
> | See what every code file does | **[docs/main.md](docs/main.md)** |
>
> The rest of this readme is a faster technical overview.

A CLI-level daily agent that makes cloud AI feel instant by firing a local nano
model's first few words immediately, then letting Claude or OpenAI finish the
response. The local model runs via llama.cpp. The cloud model continues from
exactly where the local model left off — it never restarts.

When you're not using it, it goes quiet: sessions flush to disk, the local model
unloads from RAM, and everything reloads transparently on your next message.
Over time it learns which hours you use it and can pre-warm itself before you
even open the terminal.

---

## Why

Cloud API latency is the main thing that makes AI assistants feel slow. The
first token from Claude or GPT-4 can take 1-3 seconds. A tiny local model
can produce the first 6 words in under 300ms. This project uses that gap:
show something immediately, then stream the real answer behind it.

The tradeoff is that the local model's opener is sometimes wrong. The cloud
model handles this with a recovery mode — by default it pivots naturally
without drawing attention to the mismatch.

---

## Requirements

- Python 3.10+
- A `.gguf` model file (any small instruction-tuned model, 1B-3B works well)
- An Anthropic or OpenAI API key (or both)

---

## Install

```bash
git clone <repo>
cd nano-cloud-agent
pip install -r requirements.txt

# Optional: one-shot setup of the RecursiveMAS feature
python main.py autoconfig                   # creates mas.json, .env, models/hf/
python main.py autoconfig --install-deps    # also pip install transformers
python main.py autoconfig --download        # also pre-download the HF models
```

`autoconfig` is idempotent — safe to run multiple times. It won't
clobber an existing `mas.json` unless you pass `--force`, and it
preserves any keys already in your `.env`.

For GPU acceleration of the local model:

```bash
CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python --force-reinstall
```

---

## Setup

API keys live in a gitignored `.env` file at the project root, loaded by
`agcl/secrets.py`. Copy the template and fill it in:

```bash
cp .env.example .env
# then edit .env and add your key(s)
```

Other settings are environment variables. Required: `LOCAL_MODEL_PATH` for
the local prefix model. Everything else has a working default.

```bash
export LOCAL_MODEL_PATH=models/your-model.gguf
export DEFAULT_CLOUD=claude                 # or openai
```

You can still pass keys via env vars (or any secret manager) — process env
vars override the `.env` file. Never commit `.env`.

If your model is not instruction-tuned (i.e. it's a base model, not a chat
model), also set:

```bash
export PROMPT_FORMAT=plain
```

The plain format uses `Q: ... A:` instead of ChatML tags. If you see garbled
repetitive output from the local model, this is the first thing to try.

---

## Run

Everything runs through `main.py`. The CLI auto-starts the server on first
use, so a single command is enough:

```bash
python main.py
python main.py --session work          # named session, persists across restarts
python main.py --provider openai       # force a specific cloud provider
python main.py --recovery humor        # recovery style when local prefix is off
```

To run the server in the foreground (e.g. on a separate host):

```bash
python main.py serve --port 8000
# or equivalently
uvicorn main:app --port 8000
```

---

## How a response works

1. Your message hits the server.
2. The local llama.cpp model generates the first 6 words in ~50-300ms and
   streams them to your terminal immediately with a grey latency tag.
3. The cloud model receives those 6 words as an already-open assistant turn
   and continues from there, streaming chunks as they arrive.
4. The full response (prefix + continuation) is saved to the session.

If the local prefix is off, the cloud model recovers based on the recovery mode:

- `natural` — pivots to the right answer without mentioning the mismatch (default)
- `humor` — briefly and warmly acknowledges the mismatch before correcting
- `explicit` — directly says the prefix was wrong, then answers

---

## Configuration

All settings are environment variables. None are required except your API key
and model path.

| Variable | Default | What it does |
|---|---|---|
| `LOCAL_MODEL_PATH` | `models/nano.gguf` | Path to your .gguf file |
| `LOCAL_N_CTX` | `2048` | Context window for local model |
| `LOCAL_N_GPU_LAYERS` | `0` | GPU layers, 0 = CPU only |
| `LOCAL_N_THREADS` | `4` | CPU threads for local inference |
| `PREFIX_WORD_COUNT` | `6` | Words the local model generates before handoff |
| `PROMPT_FORMAT` | `chatml` | `chatml` or `plain` depending on your model |
| `OPENAI_API_KEY` | — | OpenAI key |
| `ANTHROPIC_API_KEY` | — | Anthropic key |
| `OPENAI_MODEL` | `gpt-4o` | Which OpenAI model |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Which Anthropic model |
| `DEFAULT_CLOUD` | `claude` | Which cloud when request doesn't specify |
| `STATE_DIR` | `.agent_state` | Where sessions and patterns are saved |
| `IDLE_FLUSH_SEC` | `180` | Seconds before flushing to disk and unloading |
| `SESSION_TTL_SEC` | `3600` | Seconds before evicting session from RAM |
| `MAX_CONTEXT_TOKENS` | `6000` | Token count that triggers context compression |
| `RECONTEX_KEEP_RECENT` | `6` | Messages kept verbatim after compression |
| `PRESSURE_WINDOW_SEC` | `60` | Sliding window for request rate tracking |
| `PRESSURE_LIMIT` | `20` | Requests per window considered 100% load |
| `MIN_PATTERN_DAYS` | `3` | Days of data before an hour is considered "learned" |
| `PATTERN_LOOKBACK_DAYS` | `30` | How far back pattern history is kept |

---

## API endpoints

The server exposes these if you want to build on top of it or inspect state.

```
POST   /chat/{session_id}        streaming chat, returns SSE
GET    /session/{session_id}     view full session message history
DELETE /session/{session_id}     flush session to disk
GET    /pressure                 current API request rate and latency
GET    /patterns                 learned active hours
GET    /health                   idle time, session count, pressure summary
```

POST body for `/chat`:

```json
{
  "message": "your message",
  "provider": "claude",
  "recovery_mode": "natural"
}
```

`provider` and `recovery_mode` are optional.

SSE response format — each event is a JSON object:

```
{"type": "prefix", "text": "...", "local_ms": 87}    first, immediate
{"type": "chunk",  "text": "..."}                     many of these
{"type": "done",   "pressure": {...}}                 last
```

---

## Memory and idle behavior

The system is designed to be always-on without wasting resources.

When no requests come in for `IDLE_FLUSH_SEC` seconds (default 3 min):
- All sessions are flushed to `.agent_state/sess_*.json`
- Sessions inactive longer than `SESSION_TTL_SEC` are evicted from RAM
- The local llama.cpp model is unloaded from memory

On the next request everything reloads transparently. The local model takes
a few seconds to reload; subsequent requests within the active window are fast.

Sessions persist across full server restarts because they live on disk.

---

## Pattern learning

The agent records which hour of day you send messages. After `MIN_PATTERN_DAYS`
distinct calendar days of activity at a given hour, that hour is considered
"active". You can register callbacks in `main.py`'s startup handler that fire
during active hours on a 1-minute tick — useful for pre-loading the model
before you usually start working, or running a daily summary.

Check current learned patterns:

```bash
curl http://localhost:8000/patterns
```

---

## Troubleshooting

**Local model outputs garbage or loops ("heiz heiz heiz...")**
Your model is either a base model (not instruction-tuned) or the ChatML
template doesn't match it. Try `PROMPT_FORMAT=plain`. Also check that
`repeat_penalty` is in effect — the server console prints the raw local
model output on each request.

**JSONDecodeError in the CLI**
Update `main.py` — older versions used Python f-strings to build JSON which
produces invalid output when text contains quotes or special characters.
The fix is using `json.dumps` for all SSE payloads.

**Cloud model repeats the prefix instead of continuing**
The continuation prompt tells the cloud to never repeat the prefix, but some
models ignore this with very short prefixes. Increase `PREFIX_WORD_COUNT` to
8-10 so the continuation point is unambiguous.

**High latency on first request after idle**
Expected — the local model is reloading. Subsequent requests in the same
active window are fast. Register a pattern trigger to pre-load the model
during your known active hours to avoid this.

---

## File overview

```
config.py       all settings, read by every other file
state.py        in-memory sessions, disk flush, idle detection
pressure.py     API request rate and latency tracker
local_llm.py    llama.cpp wrapper, prefix generation, idle unload
cloud.py        OpenAI and Claude streaming with continuation logic
context.py      token counting, overflow detection, recontextualization
patterns.py     usage pattern learning, reactive hour-based triggers
recursive/      recursive multi-agent reasoning in latent space
main.py         FastAPI app + routes + idle watcher + SSE streaming + CLI client
```

Full function-level documentation for each file is in `docs/main.md`.

---

## RecursiveMAS

`agcl/recursive/` implements recursive multi-agent reasoning that
passes latent embeddings between agents instead of text. Two small
residual MLPs (`InnerLink`, `OuterLink`) sit between the agents; the loop
unrolls n rounds and only the final agent decodes text.

Two backends — pick per agent in `config.py`:

- `hf` — HuggingFace transformers. Full latent injection, full backprop.
- `gguf` — llama-cpp-python. Inference only.

Configure via env vars (`MAS_AGENTS`, `MAS_PATTERN`, `MAS_ROUNDS`,
`MAS_DEVICE`, `MAS_DTYPE`), via a JSON file (`MAS_CONFIG_FILE`), or
programmatically with `RecursiveAgent.from_pretrained` /
`RecursiveAgent.from_gguf` / `build_mas_from_specs`.

```
python main.py recursive validate    # 21 runnable checks
python main.py recursive info        # show resolved config
python main.py recursive run "..."   # one-shot
python main.py recursive run         # interactive multi-turn
```

Patterns: `sequential` (planner → critic → solver), `moe`,
`distill` (teacher → student), `deliberation`, `custom`.

### Configuration

Two ways to set up the MAS:

```
python main.py --autoconfig    # one-shot canonical 2-agent HF setup (Qwen + TinyLlama)
python main.py --config        # interactive wizard: pick pattern, agents, roles, models
```

The `--config` wizard is the manual-assisted path. It walks through:

1. collaboration pattern (sequential, moe, distill, deliberation, custom)
2. number of agents and rounds
3. per-agent backend (`hf` or `gguf`), model id/path, role, device, dtype
4. **optionally** download every HF model fully into `models/hf_local/<slug>/`
   so subsequent runs do not hit the HF Hub at all (no rate-limit warnings,
   no re-download on cache eviction)
5. write `mas.json` (with backup) and patch `.env` with `MAS_*` keys

Once the local snapshots exist, the agent specs point at the on-disk path
and `transformers` loads from there like any other directory.

### Cloud-teacher auto-training

Out of the box the inner/outer links are at random init, so an untrained
MAS injects a noise vector into the final agent and you get garbage —
typically a single `?` token. To fix this, `recursive run` boots a
`RecursiveSession` that uses the cloud model as a one-shot teacher:

1. **Bootstrap on a new topic.** First turn (and every confirmed topic
   switch) sends one cloud call asking for `(answer, [reformulations])`.
   That gives a polished answer plus 6 paraphrases of the same question.
2. **Two-stage online training.** The session then trains the inner +
   outer links (agents stay frozen):
   - *Stage A* — minimize `1 - cos(loop_final_latent, final_agent.encode(answer))`.
   - *Stage B* — teacher-force CE on the answer tokens through the final
     HF agent, with the loop's latent injected at the prompt/answer
     boundary. Skipped if the final agent is GGUF.
3. **Local follow-ups.** Subsequent turns run locally via
   `mas.generate_text`. If the output looks degenerate (too short,
   pure punctuation, single-token repetition), the session auto-falls
   back to the cloud for that turn.
4. **Topic switch detection.** Each user turn is embedded by the planner
   agent and compared (cosine) to an EMA centroid. A drop below
   `--switch-threshold` (default 0.6) triggers a yes/no cloud call to
   confirm; if confirmed, retraining runs against the new topic.
5. **Cloud override.** Prefix any turn with `/cloud ` (interactive) or
   pass `--cloud` (one-shot) to bypass the local MAS for that turn.

Tunables on `recursive run`:

```
--stage1-steps         latent-alignment step count (default 30)
--stage2-steps         token-CE step count (default 20; 0 disables)
--switch-threshold     cosine threshold for topic-switch candidate (default 0.6)
--retrieval-threshold  cosine threshold for reusing a saved topic (default 0.75)
--n-reformulations     paraphrases asked from cloud (default 6)
--cloud                one-shot: force cloud for this turn
--cloud-provider       override DEFAULT_CLOUD (openai|claude)
--continue-with-cloud  local MAS produces a short prefix; cloud finishes the turn
--prefix-tokens        prefix length in continuator mode (default 12)
--no-persist           do not save trained links / centroids to disk
```

Stage B requires the final agent to be HF-backed. With Qwen2.5-0.5B +
TinyLlama-1.1B on CPU, 30+20 steps takes roughly 1–3 minutes per topic.

### Training persistence and topic retrieval

After a topic finishes training, the session writes a small directory:

```
.agent_state/mas_topics/<topic_id>/
    meta.json       seed_question, dim signature, role/round info, timestamps
    centroid.pt     planner agent's encoding of the seed question (1-D float32)
    links.pt        state_dict of mas.inner + mas.outer (link weights only)
```

When a new turn doesn't match the running EMA centroid, the session
queries this index by the planner-embedding of the user turn (cosine sim
against every saved topic's centroid; signatures with mismatched per-
agent dims are filtered out — `OuterLink` shapes are baked in). If the
top match scores above `--retrieval-threshold`, the saved link weights
are loaded and the cloud bootstrap + training are skipped entirely.

The point is to keep the recursive loop's *attention* lightweight: the
agents stay frozen, only the small projection MLPs move, and all that
moving state is one `torch.save` per topic. Returning to a previously
seen subject costs one cosine sim + one `torch.load` instead of a
~1-3 min training pass.

Topic-management directives in interactive mode:

```
/topics              list every saved topic (id, dims, seed)
/cloud <msg>         skip local entirely, answer with cloud
/continue <msg>      one-turn cloud-continuator (local prefix + cloud finish)
/quit                exit
```

### Cloud as continuator inside the MAS

By default the MAS finishes the answer locally and only falls back to
cloud on degenerate output. `--continue-with-cloud` flips that: the
MAS produces a short prefix (`--prefix-tokens` tokens), the cloud picks
up that open assistant turn and streams the rest. This mirrors the
main agent's local→cloud handoff, just at the MAS level. Per-turn
override is `/continue <msg>`.

Deeper docs:

- [`docs/configuration.md`](docs/configuration.md) — every config
  knob explained in plain English
- [`docs/training.md`](docs/training.md) — exactly what auto-training
  does on each turn (the `[stage A]` / `[stage B]` lines you see in
  the terminal)
- [`docs/integration.md`](docs/integration.md) — node-server API for
  GUI integration (bearer auth, CORS, full `/node/*` reference, SSE
  events, every editable config mapped)
- [`docs/advanced_guide.md`](docs/advanced_guide.md) — picking models,
  mixing HF + GGUF, manual training, all 4 patterns
- [`docs/recursive_mas_setup.md`](docs/recursive_mas_setup.md) — terse
  technical reference
- [`docs/main.md`](docs/main.md) — per-file code reference

---

## Node mode (open this PC to a GUI)

To use agcl as a backend for a separate GUI / web app, run it in
**node mode**:

```bash
python main.py node --port 9876
```

This starts a FastAPI server with bearer-token auth and CORS enabled,
exposes the full editable config + the recursive MAS runtime + topic
index over HTTP, and prints a one-time auth key. Paste that key into
your GUI to authorize. Full integration contract (every endpoint,
SSE event format, editable-config table) is in
[`docs/integration.md`](docs/integration.md).