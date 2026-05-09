# AGCL mini-model background trainer

An optional, **toggleable**, **pause/resume-able** tiny model that
trains itself in the background off the latents and reasoning
trajectories AGCL produces during normal use. The goal is *not* to
replace the recursive MAS - it's a parallel learner that you can test
whenever you like, and that gradually distills the patterns you've
been steering the main engine toward.

The feature is **off by default**. Enable it with
`MINI_ENABLED=1`, the TUI menu, or `/mini-start` from any shell mode.
When off, none of its code paths execute.

---

## What it learns from

Every turn through `RecursiveSession.turn()` enqueues a sample with up
to four channels (whichever are available for that turn):

| Channel | Source | When |
|---|---|---|
| `prompt`         | the user message            | every turn |
| `output`         | the final answer string     | every turn |
| `latent`         | most-recent `TrainingControl.record_latent()` snapshot from the recursive MAS loop | only after a training/bootstrap turn |
| `reformulations` | cloud-generated paraphrases | only on bootstrap turns |
| `reasoning`      | the cloud's polished answer used for cloud-teacher training | only on bootstrap turns |

Steady-state turns contribute (prompt, output, optional latent).
Bootstrap turns contribute the full set, which is much higher signal.

---

## Architecture

Two switchable arches via `MINI_ARCH`:

### `transformer` (default)

Tiny decoder-only transformer with configurable attention. Defaults:

| | Default | Env var |
|---|---|---|
| Hidden size  | 128 | `MINI_HIDDEN`     |
| Layers       | 2   | `MINI_LAYERS`     |
| Heads        | 4   | `MINI_HEADS`      |
| Max seq      | 256 | `MINI_MAX_SEQ`    |
| Vocab        | 8192 (bucketed cl100k) | `MINI_VOCAB`      |

That's roughly **2-3M parameters** - cheap on CPU.

#### Attention presets (`MINI_ATTENTION`)

| Preset | Mask shape | Use when |
|---|---|---|
| `causal`   | lower-triangular | classic autoregressive (default) |
| `full`     | bidirectional | want encoder-style; only safe with non-CE strategies |
| `sliding`  | causal + last `MINI_WINDOW` tokens | very long sequences, local attention |
| `banded`   | bidirectional within +/- `window/2` | symmetric local context |
| `dilated`  | causal + powers-of-two strides up to `window` | sparse long-range, cheap |

### `mlp`

Two-layer MLP that maps mean-pooled prompt embedding to a learned
hidden vector. Cannot generate text usefully - useful only for cheap
**latent distillation**: train it on `latent_only` and use
`/mini-test` to inspect whether it's learning the latent geometry.

---

## Training strategies (`MINI_STRATEGY`)

A strategy is a list of (loss, weight) pairs. Each step composes the
selected losses, skipping any whose required channel is missing in
this batch.

| Preset | Components | Notes |
|---|---|---|
| `ce_only`          | `[(ce, 1.0)]` | Plain next-token CE on the answer. |
| `latent_only`      | `[(distill_latent, 1.0)]` | Only learn the latent geometry. |
| `ce_plus_latent`   | `[(ce, 1.0), (distill_latent, 0.5)]` | Default. Best balance. |
| `full`             | `[(ce, 1.0), (distill_latent, 0.5), (reform_ce, 0.3)]` | Add reformulations as additional CE targets. |
| `kl_distill`       | `[(kl, 1.0), (ce, 0.2)]` | KL distillation when teacher logits are present. |
| `contrastive_only` | `[(contrastive, 1.0)]` | InfoNCE on (prompt, output) pairs. |

---

## Background-training discipline

The trainer is a single daemon thread that:

1. Drops to nice level `MINI_NICE` (default 15) on POSIX.
2. Halves `torch.set_num_threads()` so it doesn't fight the main MAS.
3. Sleeps `MINI_YIELD_MS` (default 200 ms) between steps.
4. Skips a step if the buffer has < `MINI_BATCH_SIZE` samples.
5. Clips gradients to ||g|| <= 1.0 so a runaway sample can't trash the
   model.
6. Checkpoints to `MINI_STATE_DIR/model.pt` every `MINI_CKPT_INTERVAL`
   steps (50 by default).
7. Catches every exception in the loop body, records it on
   `status.error`, and keeps going.

Pausing flips a `threading.Event`; the loop checks it every 0.5 s.
Stopping joins the thread with a 5-second timeout.

---

## Toggling and configuring

### Auto-start

`MINI_ENABLED=1 MINI_AUTOSTART=1` (defaults when enabled): the first
`append_sample()` after AGCL boots starts the trainer. Disable with
`MINI_AUTOSTART=0` if you'd rather start it explicitly.

### From the TUI

```
> minimodel             # main-menu entry
  status / start / stop / pause / resume
  test                  # generate from the current weights
  config                # print MiniConfig
  presets               # show all strategies + attention presets
```

`/mini-*` slash commands work from any sub-mode (`/mini-status`,
`/mini-test hello`, etc.).

### From the CLI (one-shot)

```bash
python main.py mini status
python main.py mini start
python main.py mini test "explain entropy" --max-new 24
python main.py mini config
python main.py mini presets
```

### From HTTP

See [plugins.md](plugins.md#7-mini-model-control-endpoints).

### Hot-editable knobs

`PATCH /node/mini/config` (or edit the `MiniConfig` dataclass in
process):

- Cheap to change: `lr`, `strategy`, `batch_size`, `yield_ms`,
  `nice`, `ckpt_interval`, `buffer_size`, `enabled`, `autostart`.
- Requires rebuild (auto-handled by `runtime.rebuild()`):
  `arch`, `hidden`, `layers`, `heads`, `max_seq`, `vocab_size`,
  `attention`, `window`.

---

## Testing the model

```bash
python main.py mini test "what is recursion?"
```

Returns the generated continuation plus metadata:

```
  step=137 arch=transformer strategy=ce_plus_latent
  decoded > ' itself the next time...'
```

The model is tiny - don't expect coherent prose. Look at:

- `step` is climbing (training is ingesting data).
- `last_loss` from `/mini-status` is trending down over the recent
  window.
- `decoded` is producing on-vocabulary tokens, not constant outputs.

The mini model is a **distillation target**, not a competitor to the
main MAS. Its value is showing you what the system is converging
toward.

---

## File layout

```
agcl/mini/
    __init__.py
    config.py         MiniConfig dataclass + REBUILD_FIELDS
    attention.py      ATTENTION_MASKS preset registry
    model.py          MiniModel (transformer) + MiniMLP + build_arch
    strategies.py     PRESETS + compose_loss + individual losses
    trainer.py        MiniTrainer (background thread, buffer, checkpoint)
    tokenizer.py      bucketed cl100k tokenizer with hash-trick fallback
    runtime.py        process-wide singleton + append_sample hook
.agent_state/mini/
    model.pt          torch checkpoint (model + opt + step + config)
    meta.json         step count, last_loss, samples_seen, saved_at
```

---

## Caveats

- The bucketed tokenizer (cl100k mod `MINI_VOCAB`) is lossy on decode.
  Use `/mini-test` to sanity-check learning, not to read prose out.
- A long-running `/mas/stream` will compete with the trainer for CPU
  even at `nice 15`. Pause the trainer (`/mini-pause`) before doing a
  cold-start training run if you want it to finish faster.
- Checkpoints are not portable across `MINI_ARCH` or hidden-size
  changes. Changing those triggers `runtime.rebuild()` and starts
  fresh.
