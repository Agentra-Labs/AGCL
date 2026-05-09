# Configuration — what every knob does, in plain English

This doc explains every setting in agcl and what changes when you
flip it. It's organized by **what you might want to do** rather than by
file, so you can scan for your goal and find the right knob.

It assumes you've at least run the project once. If not, start at
**[guide.md](guide.md)**.

> **Where you are:**  this is the configuration walkthrough.
> Other docs:
> - **[guide.md](guide.md)** — first-time setup
> - **[training.md](recursive/training.md)** — what auto-training actually does
> - **[gui.md](gui.md)** — node API for GUI integration
> - **[recursive/advanced.md](recursive/advanced.md)** — picking models and
>   patterns
> - **[recursive/setup.md](recursive/setup.md)** — terse
>   reference for the recursive feature
> - **[main.md](code-map.md)** — per-file code reference

---

## How config layers work

agcl checks settings in this order:

```
1. command-line flag      (highest priority — wins)
        ↓
2. process env var        (export FOO=bar in your shell)
        ↓
3. .env file              (KEY=VALUE per line, project root)
        ↓
4. default in config.py   (lowest priority — fallback)
```

Each later layer fills in only what the earlier ones didn't set. So you
can leave most things at defaults and only override what matters to you.

Keys live in a few places:

- **`.env`** — secrets and personal preferences (API keys, model paths).
  Gitignored. Created from `.env.example`.
- **`mas.json`** — agent recipe (which models, what roles). Gitignored
  by default.
- **CLI flags** — per-run overrides that don't deserve a permanent home.

You almost never edit `agcl/config.py` directly. It's the
defaults-of-last-resort, not a place to put your settings.

---

## I just want to run the basic agent

Three settings matter:

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...        # OR OPENAI_API_KEY=...
LOCAL_MODEL_PATH=models/your.gguf   # the small local model
```

If both API keys are set, you can pick which one to use:

```bash
DEFAULT_CLOUD=claude    # or openai
```

Run:

```bash
python main.py
```

That's it. Everything else has a sensible default.

---

## I want to set up the recursive multi-agent feature

There are **two paths**, depending on how much you want to customize.

### Path 1: one-shot canonical setup (`--autoconfig`)

```bash
python main.py --autoconfig
```

This installs `mas.json` with two HuggingFace agents: Qwen2.5-0.5B as
planner, TinyLlama-1.1B as solver. Sequential pattern, 2 rounds. It
also patches `.env` with the right `MAS_*` keys.

Use this if you just want the canonical setup and trust the defaults.

### Path 2: interactive wizard (`--config`)

```bash
python main.py --config
```

This walks you through every choice:

```
patterns:
  sequential    fixed pipeline, e.g. planner -> critic -> solver
  moe           mixture-of-experts; each agent contributes per round
  distill       exactly 2 agents: teacher -> student
  deliberation  >=2 agents, debate over >=3 rounds
  custom        free-form; you assign every role yourself

  pattern (sequential|moe|distill|deliberation|custom) [sequential]:
  number of agents [2]:
  number of rounds (loop unrolls) [2]:
  ...
```

For each agent you'll be asked:

```
  backend (hf|gguf) [hf]:
  HF model id (or local path) [Qwen/Qwen2.5-0.5B-Instruct]:
  role (free-form) [planner]:
  device (cpu|cuda) [cpu]:
  dtype (float32|float16|bfloat16) [float32]:
```

Press **Enter** to accept any default in `[brackets]`.

At the end, you get one bonus question:

```
download all HF models now? (y|n) [n]:
```

If you say yes, every HF model in your config is fully downloaded into
`models/hf_local/<slug>/` and the spec is rewritten to point at that
on-disk location. **Subsequent runs do not hit the HuggingFace Hub at
all** — no rate-limit warnings, no re-download on cache eviction. This
is the right call if you want the project to be entirely
self-contained.

Use `--config` when:
- You want a non-default pattern (`moe`, `distill`, etc.).
- You want different models than the canonical pair.
- You want different roles ("teacher"/"student" instead of "planner"/
  "solver", say).
- You want models fully on disk, not in HF cache.

You can re-run `--config` any time to make a new setup; an existing
`mas.json` is backed up to `mas.json.bak` before being overwritten.

---

## What is `mas.json` actually?

It's a flat list of agent specs. Here's a fully-annotated example:

```json
{
  "agents": [
    {
      "backend": "hf",
      "model":   "Qwen/Qwen2.5-0.5B-Instruct",
      "role":    "planner",
      "device":  "cpu",
      "dtype":   "float32"
    },
    {
      "backend":      "gguf",
      "model":        "models/SmolLM2-135M.Q2_K.gguf",
      "role":         "critic",
      "n_ctx":        2048,
      "n_gpu_layers": 0,
      "n_threads":    4
    },
    {
      "backend": "hf",
      "model":   "models/hf_local/TinyLlama_TinyLlama-1.1B-Chat-v1.0",
      "role":    "solver",
      "device":  "cpu",
      "dtype":   "bfloat16"
    }
  ]
}
```

Three agents. The middle one is GGUF (cheap, fast, no training). The
last one is loaded from a local snapshot — note the path under
`models/hf_local/` instead of an HF id. This is what `--config` writes
when you pick "download locally".

The order matters: agents run in this order each round. The **last**
one is the one that produces text.

### What happens if you change the order?

Each agent has different "thinking dimensions" (hidden size). Change
the order and the projection MLPs between them have different shapes.
Saved trained-link files become incompatible (signature mismatch — see
[training.md](recursive/training.md#step-2--resolving-a-topic-retrieve-or-train)).

In practice: changing `mas.json` means saved topics for the old setup
won't load. They're not deleted, just ignored. To clean up:

```bash
rm -rf .agent_state/mas_topics/
```

### Each field, slowly

| Field | Required | For backend | What it does |
|---|:---:|:---:|---|
| `backend` | yes | both | `"hf"` (HuggingFace) or `"gguf"` (llama.cpp) |
| `model` | yes | both | HF id, local HF path, or `.gguf` path |
| `role` | no | both | Just a label (e.g. "planner"). Used in `--config` defaults and `info` output |
| `device` | no | hf | `"cpu"` / `"cuda"` / `"cuda:0"` |
| `dtype` | no | hf | `"float32"` / `"float16"` / `"bfloat16"` |
| `n_ctx` | no | gguf | Context window. Bigger = slower, more RAM |
| `n_gpu_layers` | no | gguf | Layers to offload to GPU. `0` = pure CPU |
| `n_threads` | no | gguf | CPU threads for inference. `4` is a good start |

---

## Picking models — what affects what?

### Bigger models = slower + more RAM, but better quality

| Model | Hidden | RAM (float32) | RAM (bfloat16) |
|---|:---:|:---:|:---:|
| SmolLM2-135M-Instruct | 576 | ~540 MB | ~270 MB |
| SmolLM2-360M-Instruct | 768 | ~1.4 GB | ~720 MB |
| Qwen2.5-0.5B-Instruct | 896 | ~2.0 GB | ~1.0 GB |
| Qwen2.5-1.5B-Instruct | 1536 | ~6.0 GB | ~3.0 GB |
| TinyLlama-1.1B-Chat | 2048 | ~4.4 GB | ~2.2 GB |

Quick rule of thumb:

- **Two SmolLM2-135M agents** = pure smoke test. Fast, terrible
  output. Useful for verifying the loop wires up.
- **Qwen 0.5B + TinyLlama 1.1B** (the canonical pair) = decent
  output. Runs fine on a CPU laptop with 8 GB RAM.
- **Qwen 1.5B + Qwen 1.5B** = noticeably better. Wants 16 GB or
  GPU.

### `dtype` — precision tradeoff

- **`float32`** — 32 bits per number. Most precise, most RAM.
- **`bfloat16`** — 16 bits, brain-float layout. Half the RAM, almost
  no quality loss. **Recommended on any modern CPU.**
- **`float16`** — 16 bits, IEEE layout. Half the RAM, **can be
  numerically unstable on CPU.** Use only on GPU.

If you have RAM pressure, switch every HF agent to `bfloat16`. It's
the cheapest single change that makes a difference.

### `device` — where the model lives

- `cpu` — works everywhere, slow on big models.
- `cuda` — needs an NVIDIA GPU + CUDA build of PyTorch. Way faster.
  See <https://pytorch.org/get-started/locally/> for install.
- `cuda:0`, `cuda:1`, ... — pick a specific GPU when you have
  multiple.

You can mix: planner on `cuda`, critic on `cpu`, etc. Useful when one
model fits in GPU memory but the other doesn't.

---

## Patterns — what does each one actually do?

The pattern label is mostly metadata. The recursive loop is wired the
same way regardless. What differs is **how many agents you bring** and
**how many rounds the loop runs**.

| Pattern | Agents | Default rounds | Mental model |
|---|:---:|:---:|---|
| `sequential` | 1+ | 2 | Pipeline: planner → critic → solver |
| `moe` | 1+ | 2 | Several specialists, all weighing in |
| `distill` | exactly 2 | 2 | Big teacher informing a small student |
| `deliberation` | 2+ | ≥ 3 | Debate; needs more rounds to settle |
| `custom` | 1+ | 2 | Free-form; you decide |

If you're just experimenting, pick `sequential`. The others mostly
matter when training on real data.

`MAS_ROUNDS` (default `2`) controls how many times the loop unrolls.

- **1 round** = each agent runs once. Cheapest, "thinnest" reasoning.
- **2 rounds** = default. Each agent sees the others' refined latents.
- **3+ rounds** = slower; sometimes better, sometimes overfits.

Each extra round multiplies inference time by roughly the agent count.
If 2 rounds takes 10 seconds, 3 rounds takes ~15.

---

## Local models vs HF cache

You have three places HF models can live:

### 1. The HF Hub (lazy download)

Default. The first run pulls the model from huggingface.co into a
cache directory. Subsequent runs use the cache. The cache is in
`models/hf/` inside this project (set by agcl via `HF_HOME`).

Pros: zero setup. Cons: first run waits for download; HF's free tier
rate-limits unauthenticated requests; cache can be evicted.

### 2. Local snapshot (full download)

```bash
python main.py --config
# answer "y" to the "download all HF models now?" question
```

Each HF model is fully downloaded into
`models/hf_local/<slug>/` and the spec in `mas.json` is rewritten to
point at that path:

```json
"model": "models/hf_local/Qwen_Qwen2.5-0.5B-Instruct"
```

Pros: never hits HF after this. Self-contained. No rate limits. Cons:
takes disk (~1-5 GB per model). One-time download cost.

### 3. Your own existing path

If you already have an HF model dir on disk somewhere, just use that
path:

```json
"model": "/home/me/llama-models/qwen-0.5b/"
```

Works the same as a snapshot.

### Where do GGUF files live?

Anywhere, but conventionally `models/your.gguf`. The path in
`mas.json` is just resolved relative to where you ran `python main.py`
from.

---

## Where state goes (the on-disk layout)

```
.agent_state/                          ← STATE_DIR
    sess_default.json                  ← chat sessions (main agent)
    sess_work.json                     ← named sessions
    patterns.json                      ← learned active hours
    mas_topics/                        ← trained MAS state
        92ff1629f157/
            meta.json
            centroid.pt
            links.pt
        a3b9c7e85dee/
            meta.json
            centroid.pt
            links.pt

models/
    hf/                                ← HF cache (lazy)
    hf_local/                          ← full local snapshots
        Qwen_Qwen2.5-0.5B-Instruct/
        TinyLlama_TinyLlama-1.1B-Chat-v1.0/
    your-local.gguf                    ← any GGUF files you downloaded

mas.json                               ← agent recipe
.env                                   ← secrets + overrides
```

The whole thing is portable: copy the project + `.agent_state/` +
`models/` to another machine and it'll resume right where you left off.

---

## Common scenarios

### "I want maximum quality, don't care about speed"

```json
// mas.json
{
  "agents": [
    {"backend": "hf", "model": "Qwen/Qwen2.5-1.5B-Instruct",
     "role": "planner", "dtype": "bfloat16"},
    {"backend": "hf", "model": "Qwen/Qwen2.5-1.5B-Instruct",
     "role": "critic",  "dtype": "bfloat16"},
    {"backend": "hf", "model": "meta-llama/Llama-3.2-3B-Instruct",
     "role": "solver",  "dtype": "bfloat16"}
  ]
}
```

```bash
# .env
MAS_ROUNDS=3
```

```bash
python main.py recursive run --stage1-steps 60 --stage2-steps 40 --continue-with-cloud
```

That's: three big models, three rounds, more training, plus cloud
finishing the answer. Slow (~minutes per topic) but high quality.

### "I want minimum RAM, just to play with it"

```json
{
  "agents": [
    {"backend": "hf", "model": "HuggingFaceTB/SmolLM2-135M-Instruct",
     "role": "planner", "dtype": "bfloat16"},
    {"backend": "hf", "model": "HuggingFaceTB/SmolLM2-360M-Instruct",
     "role": "solver",  "dtype": "bfloat16"}
  ]
}
```

Two small models, bfloat16. Total RAM ~1 GB. Output is rough but it
runs anywhere.

### "I want to plug in a GGUF model I already have"

```json
{
  "agents": [
    {"backend": "gguf", "model": "models/my-model.Q4_K_M.gguf",
     "role": "planner", "n_ctx": 2048, "n_threads": 8},
    {"backend": "hf", "model": "Qwen/Qwen2.5-0.5B-Instruct",
     "role": "solver", "dtype": "bfloat16"}
  ]
}
```

GGUF first (cheap, fast), HF last (good text output, supports
training). Best of both.

### "I want offline mode after first run"

Run `--config` and say yes to "download all HF models locally". Then
`mas.json` will have local paths and HF won't be contacted again. As
long as your `.gguf` files are already downloaded, you can airplane
mode the rest.

### "I want to share trained topics with another machine"

Just copy `.agent_state/mas_topics/` to the other machine. The MAS
config (dim signatures) has to match — same models, same order, same
dtype.

---

## Tunables on `recursive run`

These are CLI-only — they don't live in `.env` or `mas.json` because
they're per-run choices, not persistent ones.

| Flag | Default | Effect |
|---|---|---|
| `--cloud` | off | Force this turn through cloud (one-shot). Skip local entirely |
| `--cloud-provider claude\|openai` | from `DEFAULT_CLOUD` | Which provider to use |
| `--continue-with-cloud` | off | Local makes a short prefix, cloud finishes |
| `--prefix-tokens N` | 12 | How many tokens local makes in continuator mode |
| `--max-new-tokens N` | 128 | Max tokens local makes in non-continuator mode |
| `--stage1-steps N` | 30 | Auto-training stage A iterations |
| `--stage2-steps N` | 20 | Auto-training stage B iterations (0 disables) |
| `--switch-threshold F` | 0.6 | Cosine sim below this triggers a topic-switch check |
| `--retrieval-threshold F` | 0.75 | Cosine sim above this reuses a saved topic |
| `--n-reformulations N` | 6 | Question paraphrases asked from cloud |
| `--no-persist` | off | Don't save trained links / centroids to disk |

For the conceptual meaning of each, see
**[training.md](recursive/training.md#tunables--what-to-change-and-when)**.

---

## In-session directives

Once you're at `you:` in interactive mode, you have a few escape
hatches:

| Type this | What it does |
|---|---|
| `/cloud here is my message` | Skip local for this turn; use cloud |
| `/continue here is my message` | Local prefix + cloud finish (one-shot) |
| `/topics` | List saved topics you can reuse |
| `/quit` | Exit cleanly |

These work even mid-conversation. They don't change the session config
permanently — only that one turn.

---

## Full env-var reference

For completeness. Every setting you can put in `.env` or export in
your shell:

```text
# main agent
LOCAL_MODEL_PATH       path to your .gguf file
LOCAL_N_CTX            context window for local model         (2048)
LOCAL_N_GPU_LAYERS     GPU layers, 0 = CPU only                (0)
LOCAL_N_THREADS        CPU threads for local inference         (4)
PREFIX_WORD_COUNT      words local makes before cloud takes over (4)

# cloud
OPENAI_API_KEY         OpenAI key
ANTHROPIC_API_KEY      Anthropic key
OPENAI_MODEL           which OpenAI model                      (gpt-4o)
CLAUDE_MODEL           which Anthropic model                   (claude-sonnet-4-20250514)
DEFAULT_CLOUD          openai | claude                         (claude)

# storage
STATE_DIR              where sessions + topics live            (.agent_state)
IDLE_FLUSH_SEC         flush + unload local model after silence (180)
SESSION_TTL_SEC        evict session from RAM after            (3600)

# context handling
MAX_CONTEXT_TOKENS     trigger context compression at          (6000)
RECONTEX_KEEP_RECENT   keep this many turns verbatim post-compress (6)

# pattern learning
MIN_PATTERN_DAYS       distinct days before hour is "learned"  (3)
PATTERN_LOOKBACK_DAYS  history kept                            (30)

# pressure tracking
PRESSURE_WINDOW_SEC    window size for rate                    (60)
PRESSURE_LIMIT         requests per window = 100% load         (20)

# recursive MAS (these come from --autoconfig or --config)
MAS_AGENTS             JSON literal (overrides mas.json)
MAS_CONFIG_FILE        path to mas.json
MAS_PATTERN            sequential | moe | distill | deliberation | custom
MAS_ROUNDS             how many rounds the loop unrolls
MAS_DEVICE             default device for hf agents that don't set their own
MAS_DTYPE              default dtype for hf agents
HF_HOME / HF_HUB_CACHE override HF cache directory
```

---

## Where to go next

- **[training.md](recursive/training.md)** — what auto-training is doing under
  the hood
- **[recursive/advanced.md](recursive/advanced.md)** — picking models, mixing
  HF + GGUF, training tutorials
- **[recursive/setup.md](recursive/setup.md)** — the dense
  technical spec
- **[main.md](code-map.md)** — per-file code reference
