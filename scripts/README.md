# scripts/

Helper scripts that aren't part of the main package — run them directly.

## validate_hf_training.py

Real end-to-end validation of an HF-backed RecursiveMAS. Loads the
agents listed in your active config (env var, `mas.json`, or the
`config.py` default), then runs three checks:

1. Both agents load and report their hidden sizes.
2. `generate_text("Explain backpropagation in one sentence.")` returns
   a non-empty string.
3. `stage2_full_loop` runs `--steps` real training steps on synthetic
   data and the loss must move by > 1e-3 (proves gradients flow
   through every link and every agent).

```bash
python scripts/validate_hf_training.py
python scripts/validate_hf_training.py --skip-train          # inference only
python scripts/validate_hf_training.py --steps 20 --lr 1e-4  # more training
python scripts/validate_hf_training.py --prompt "your text"
```

What "loss moved" means:
training on random targets won't actually converge — but if the loss
is exactly the same after 8 steps, no gradient is reaching anything.
A small movement (any direction) is the signal we want.

First run downloads any missing models to `~/.cache/huggingface/`.
With the default Qwen2.5-0.5B + TinyLlama-1.1B config that's about
3 GB once.
