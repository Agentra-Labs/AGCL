# Advanced guide — running RecursiveMAS with real HuggingFace models

This guide picks up where [`guide.md`](guide.md) leaves off. It walks
through standing up a real recursive multi-agent system with
HuggingFace models, mixing them with GGUF models, and tuning the loop.

It still goes step by step, but it assumes you've already done the
beginner setup at least once (Python installed, `env/` activated,
`pip install -r requirements.txt` succeeded, basic chat working).

About 30–45 minutes start to finish, plus model download time. Disk
space depends on which models you pick — anywhere from 300 MB to
several GB.

---

## What you're about to build

Several models reasoning together. Instead of each one writing text
that the next one reads, they pass internal "thoughts" (embedding
vectors) directly to each other. Only the last model writes anything
to the screen. The result is faster, and the models share gradient
when you train.

A typical setup looks like this:

    your prompt
        → planner   (HF, small)
            → critic    (HF, small)
                → solver    (HF, larger; produces the final text)

Or mixed:

    your prompt
        → planner   (HF, small)
            → cheap critic   (GGUF, fast inference)
                → solver         (HF, full model)

You pick the models, the roles, and how many "rounds" they loop for.

---

## Shortcut — one-command setup

If you want the canonical setup this whole guide builds toward
(Qwen2.5-0.5B + TinyLlama-1.1B, sequential, 2 rounds), one command
puts every file in place:

    python main.py autoconfig --install-deps --download

That does steps 1, 4, 5, and the model download in one shot. Skip
ahead to step 6 once it finishes.

If you'd rather understand each piece (or want a different model
mix), keep reading from step 1 — `autoconfig` is just a wrapper
around what the rest of this guide does manually.

---

## Step 1 — Install transformers

The base project doesn't pull in HuggingFace `transformers` because
it's heavy. You need it for any HF agent.

Make sure your virtual environment is active (`(env)` should be in
your prompt). Then:

    pip install transformers

This will also pull in `tokenizers`, `safetensors`, and a few others.
Takes a minute.

If you have a GPU and want to use it, also install the matching CUDA
build of PyTorch — see <https://pytorch.org/get-started/locally/>.
Otherwise CPU is fine for small models.

Verify:

    python -c "import transformers; print(transformers.__version__)"

You should see a version number like `4.45.x`.

---

## Step 2 — Pick your models

You have two backends:

| Backend | Source             | Latent injection | Training |
|---------|--------------------|:----------------:|:--------:|
| `hf`    | HuggingFace Hub    | **yes**          | **yes**  |
| `gguf`  | local `.gguf` file | no¹              | no       |

¹ GGUF agents still participate in the loop; they just can't condition
on a previous agent's latent at their own input. Their output still
flows to the next agent.

**Rule of thumb:**
- Use `hf` when you want quality + the ability to train the loop.
- Use `gguf` when you want very cheap intermediate steps, or when the
  best version of your model only ships as a `.gguf`.

### A short list of HF models worth trying

These are all small enough to run on a laptop CPU. Bigger is better
for output quality but slower.

| Model id                                    | Size   | Notes                            |
|---------------------------------------------|--------|----------------------------------|
| `HuggingFaceTB/SmolLM2-135M-Instruct`       | ~270 MB | Tiny — useful for testing the loop wiring |
| `HuggingFaceTB/SmolLM2-360M-Instruct`       | ~720 MB | Surprisingly capable for size    |
| `Qwen/Qwen2.5-0.5B-Instruct`                | ~1 GB   | Strong all-rounder               |
| `Qwen/Qwen2.5-1.5B-Instruct`                | ~3 GB   | Good "main agent" pick           |
| `meta-llama/Llama-3.2-1B-Instruct`          | ~2.5 GB | Needs HF login + model access    |
| `microsoft/Phi-3.5-mini-instruct`           | ~7.5 GB | Strong; needs more RAM           |

For your first real run, **start small** — pick two of the SmolLM2
sizes or Qwen 0.5B. You can always swap in bigger ones later by
changing one line in your config.

### How HF model ids work

`Qwen/Qwen2.5-0.5B-Instruct` — that whole string is the id. The first
half (`Qwen/`) is the organization, the second half is the model.
You can paste it straight into our config — `transformers` will
download and cache it on first use.

### Where HF models get cached

By default this project caches HF downloads to `models/hf/` inside the
project root (set automatically in `config.py` via `HF_HOME`). The
folder is gitignored, so models stay out of git, and one
`rm -rf models/hf/` cleans every download. To use the system-wide
cache instead, override:

    export HF_HOME=~/.cache/huggingface
    # or point HF_HUB_CACHE wherever you want

If you want to peek before downloading, paste the id at the end of
`https://huggingface.co/` (e.g.
<https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct>).

### Some models need login

Llama family (Meta), Gemma family (Google), and a few others are
"gated" — you need a HuggingFace account, accept their license on the
model page, and log in locally:

    huggingface-cli login

Paste an access token from <https://huggingface.co/settings/tokens>.
After this, gated models will download for you the same way as open
ones.

---

## Step 3 — Decide what each agent does (the pattern)

The recursive loop has the same wiring no matter how many agents you
use. But you can label what each one is "for" and that helps you keep
track. Pick one of these:

| Pattern        | Roles                              | Use when…                   |
|----------------|------------------------------------|-----------------------------|
| `sequential`   | planner → critic → solver          | a normal pipeline           |
| `moe`          | expert_0, expert_1, ...            | several specialists         |
| `distill`      | teacher → student (exactly 2)      | training a student model    |
| `deliberation` | reflector + tool_caller (≥3 rounds)| back-and-forth thinking     |
| `custom`       | whatever you like                  | you know what you want      |

Pick `sequential` if you're just experimenting.

---

## Step 4 — Write your config

There are three places config can live, checked in order: an env var,
a JSON file, or the default in `config.py`. The JSON file is by far
the easiest for a real setup.

Make a file at the project root called `mas.json`:

    {
      "agents": [
        {
          "backend": "hf",
          "model": "HuggingFaceTB/SmolLM2-135M-Instruct",
          "role": "planner",
          "device": "cpu",
          "dtype": "float32"
        },
        {
          "backend": "hf",
          "model": "HuggingFaceTB/SmolLM2-360M-Instruct",
          "role": "solver",
          "device": "cpu",
          "dtype": "float32"
        }
      ]
    }

Then in your `.env` file, point at it:

    MAS_CONFIG_FILE=./mas.json
    MAS_PATTERN=sequential
    MAS_ROUNDS=2

Save both files.

### Per-agent options

Every agent dict supports these fields:

```text
backend     "hf" or "gguf"               (required)
model       HF id, local path, or .gguf  (required)
role        any string label             (optional)

# hf-only
dtype       "float32" / "float16" / "bfloat16"
device      "cpu" / "cuda" / "cuda:0"

# gguf-only
n_ctx          context window (default 2048)
n_gpu_layers   layers offloaded to GPU (default 0)
n_threads      CPU threads (default 4)
```

`float16` and `bfloat16` halve the memory used by HF models — try
those first if RAM is tight. They only work on certain hardware
(`bfloat16` needs a recent CPU or any modern GPU; `float16` is
GPU-friendly but can be unstable on CPU).

---

## Step 5 — Confirm everything wires up

Before downloading multi-GB models, sanity-check your config:

    python main.py recursive info

You should see your two agents listed with the right backend, role,
and model name. If not, you have a typo in the JSON or the env var
isn't set — fix that before going further.

Then run the test suite:

    python main.py recursive validate

It should print `21/21 tests passed`. This doesn't load your real
models — it just checks that the loop, the math, the backends, and
the config parser all behave correctly.

---

## Step 6 — Run the MAS

This first run will download whatever HF models you listed. Expect a
progress bar; downloads are cached so it only happens once.

    python main.py recursive run "explain backprop in two sentences"

What happens behind the scenes:

1. Each agent is loaded (in order). HF models cache to
   `~/.cache/huggingface/`.
2. The loop runs `MAS_ROUNDS` times. Each round, each agent gets the
   prompt + the previous agent's latent thought.
3. Only the last agent decodes text. You see that on screen.

You'll see status lines like:

    [mas] building from config...
    [mas] 2 agents, 2 rounds, dims=[576, 960]
    [mas] running loop...
    <generated text appears here>

The numbers in `dims=` are each agent's hidden-state size. Different
sizes are fine — `OuterLink` reshapes between them automatically.

---

## Step 7 — Mix HF and GGUF

A common useful setup: use a fast quantized GGUF for an intermediate
step, and a higher-quality HF model for the final answer. Edit
`mas.json`:

    {
      "agents": [
        {
          "backend": "gguf",
          "model": "models/SmolLM2-135M.Q2_K.gguf",
          "role": "planner",
          "n_threads": 4
        },
        {
          "backend": "hf",
          "model": "Qwen/Qwen2.5-0.5B-Instruct",
          "role": "solver",
          "device": "cpu",
          "dtype": "bfloat16"
        }
      ]
    }

Things to remember:

- The **final** agent should usually be `hf` — it produces the text.
  `gguf` works as a final agent too, but it can't use the prior
  latent at its own input, so the last "thinking step" is wasted.
- Two `gguf` files share the same `.gguf` only via separate model
  loads — RAM doubles. If you want shared weights, use a single
  agent with more rounds.

---

## Step 8 — Tune the loop

Two knobs matter:

**`MAS_ROUNDS`** — how many times the agents loop before decoding.

- 1 round = each agent runs once. Fast, minimal "thinking".
- 2 rounds = the default. Each agent sees the others' refined latents.
- 3+ rounds = slower, sometimes better, can also overfit on garbage.

**Number of agents** — more agents = more capacity, more time, more RAM.

Run timing yourself:

    time python main.py recursive run "summarize relativity"

Halve `MAS_ROUNDS` or drop an agent if it's too slow.

---

## Step 9 (optional) — Memory and speed

If your machine is tight, in this order:

1. **Switch to `bfloat16`** in the spec for each HF agent. Halves RAM.
2. **Drop one agent.** Two agents is plenty for an experiment.
3. **Use smaller models.** SmolLM2-135M-Instruct is 270 MB. Two of
   them together fit on almost anything.
4. **Replace HF agents with GGUF agents.** Quantized GGUF is
   dramatically smaller and faster to run.
5. **Move to GPU.** Set `device: "cuda"` in each HF spec — but only
   after installing the CUDA build of torch.

Watch RAM with `htop` (or Activity Monitor on macOS) while it runs.
HF agents stay loaded for the lifetime of the process.

---

## Step 10 (optional) — Train the loop

This is only for HF agents (GGUF can't backprop). The training is in
`openslock/recursive/train.py` as small generators you drive yourself.

There are two stages:

**Stage 1 — warmup.** Each agent's `InnerLink` is trained alone with
its underlying LM frozen. The loss is "how close is the produced
latent thought to a target?" using cosine similarity. Quick, cheap,
gets the latent space pointed in the right direction.

**Stage 2 — full loop.** The whole MAS unrolls and you backprop on
the final-token cross-entropy. Every link and every agent receives
gradient.

A minimal sketch:

    from openslock.recursive import build_from_config, stage2_full_loop

    mas = build_from_config()         # all hf agents

    def batches():
        # yield (input_ids, attention_mask, target_ids) tuples from your data
        ...

    for step, loss in stage2_full_loop(mas, batches(), lr=1e-4, max_steps=500):
        if step % 10 == 0:
            print(step, loss)

Runnable, fully-tested examples on tiny synthetic data live in
`openslock/recursive/validate.py` (look at
`t_stage1_warmup_reduces_loss` and `t_stage2_full_loop_reduces_loss`).

You don't have to train. The MAS works fine zero-shot — training just
sharpens it for a specific task.

---

## Things that commonly go wrong

**"HFBackend requires transformers"** — you skipped step 1. `pip
install transformers`.

**`OSError: We couldn't connect to 'huggingface.co'`** — first-time
download needs internet. After that, everything's cached and offline.

**"Access to model X is restricted"** — gated model. Run
`huggingface-cli login`, accept the license on the model's page, retry.

**Out of memory** — see step 9. Start with `bfloat16` and smaller
models.

**`generate_text` returns nonsense** — RecursiveMAS doesn't fix
underlying model quality. Two SmolLM2-135M instances will still talk
like SmolLM2-135M. Use 1B+ models for real answers.

**Model downloads every run** — they shouldn't. They cache to
`~/.cache/huggingface/`. If they don't, check that variable hasn't
been overridden, and that the cache directory is writable.

**"`MAS_AGENTS` invalid JSON"** — almost always a missing comma or a
trailing comma in `mas.json`. Paste it into <https://jsonlint.com> if
you're not sure.

**Different output every time even at the same prompt** — expected.
The HF generation defaults include sampling. To get deterministic
output, pass `temperature=0` etc. through the API call (you'd need to
use `mas.generate_text(..., do_sample=False)`).

---

## Useful commands cheat-sheet

    python main.py autoconfig                          # one-shot project setup
    python main.py autoconfig --install-deps           # also pip install transformers
    python main.py autoconfig --download               # also pre-pull HF models
    python main.py autoconfig --force                  # overwrite mas.json

    python main.py recursive validate                  # run all 21 checks
    python main.py recursive info                      # show resolved config
    python main.py recursive run "your prompt"         # generate text
    python main.py recursive run "..." --max-new-tokens 64

    huggingface-cli login                              # one-time, for gated models
    huggingface-cli scan-cache                         # see what's downloaded
    huggingface-cli delete-cache                       # free disk space

---

## Where to go next

- Module reference: [main.md](main.md)
- Tighter technical spec: [recursive_mas_setup.md](recursive_mas_setup.md)
- The actual code (it's small and readable): `openslock/recursive/`
- The 21 validation tests double as runnable examples:
  `openslock/recursive/validate.py`

Once you have a config that works, the only thing you really change
day-to-day is the model ids in `mas.json`. Everything else stays put.
