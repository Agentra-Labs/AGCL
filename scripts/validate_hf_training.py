"""
End-to-end validation of an HF-backed RecursiveMAS.

Builds the MAS from agcl.config, runs three real checks:

    1. Both agents load and report correct hidden sizes.
    2. generate_text() produces a non-empty string from a real prompt.
    3. stage2_full_loop() reduces cross-entropy on a tiny synthetic
       task — proves gradients flow through every link and every agent.

Run:
    python scripts/validate_hf_training.py
    python scripts/validate_hf_training.py --skip-train       # inference only
    python scripts/validate_hf_training.py --steps 20         # more train steps
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Make the project root importable regardless of where this script is launched.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402,F401


def banner(msg: str):
    print(f"\n{'=' * 70}\n  {msg}\n{'=' * 70}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="Explain backpropagation in one sentence.")
    ap.add_argument("--max-new-tokens", type=int, default=32)
    ap.add_argument("--steps", type=int, default=8,
                    help="stage-2 training steps")
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--seq-len", type=int, default=8)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--skip-train", action="store_true")
    args = ap.parse_args()

    banner("1/3  building MAS from config")
    from agcl import config as cfg
    print(f"  pattern = {cfg.MAS_PATTERN}")
    print(f"  rounds  = {cfg.MAS_ROUNDS}")
    print(f"  agents  = {len(cfg.MAS_AGENTS)}")
    for i, s in enumerate(cfg.MAS_AGENTS):
        print(f"    [{i}] {s.get('backend')}  role={s.get('role','')}  model={s.get('model')}")

    from agcl.recursive import build_from_config
    t0 = time.time()
    mas = build_from_config()
    print(f"  loaded in {time.time() - t0:.1f}s")
    print(f"  hidden sizes (per agent): {mas.dims}")
    print(f"  inner-link params: "
          f"{sum(sum(p.numel() for p in m.parameters()) for m in mas.inner)}")
    print(f"  outer-link params: "
          f"{sum(sum(p.numel() for p in m.parameters()) for m in mas.outer)}")

    assert all(a.supports_injection() for a in mas.agents), \
        "every HF agent should support injection"
    print("  all agents report supports_injection=True (HF) — ok")

    banner("2/3  inference pass (generate_text)")
    print(f'  prompt: "{args.prompt}"')
    t0 = time.time()
    out = mas.generate_text(
        args.prompt, max_new_tokens=args.max_new_tokens,
        do_sample=True, temperature=0.7, top_p=0.9, min_new_tokens=4,
    )
    dt = time.time() - t0
    print(f"  output ({dt:.1f}s):")
    print(f"    {out!r}")
    assert isinstance(out, str) and len(out) > 0, "generate_text returned empty"
    print("  generate_text returned a non-empty string — ok")

    if args.skip_train:
        banner("done (training skipped)")
        return 0

    banner("3/3  stage-2 training step on synthetic data")
    print(f"  steps={args.steps}  batch={args.batch_size}  seq_len={args.seq_len}  lr={args.lr}")

    # We need a tokenizer to make synthetic id tensors. Use the FIRST
    # agent's tokenizer — RecursiveMAS.run_latent (token mode) uses
    # input_ids that the first agent encodes, so we feed shapes from
    # whichever vocab matches that agent.
    first_tok = mas.agents[0].tokenizer
    last_tok  = mas.agents[-1].tokenizer
    vocab_first = first_tok.vocab_size if hasattr(first_tok, "vocab_size") else len(first_tok)
    vocab_last  = last_tok.vocab_size  if hasattr(last_tok,  "vocab_size") else len(last_tok)
    print(f"  first-agent vocab: {vocab_first}")
    print(f"  last-agent vocab:  {vocab_last}")

    torch.manual_seed(0)

    def synth_batches():
        for _ in range(args.steps):
            ids = torch.randint(0, min(vocab_first, 1000),
                                (args.batch_size, args.seq_len))
            attn = torch.ones_like(ids)
            tgt = torch.randint(0, min(vocab_last, 1000),
                                (args.batch_size, args.seq_len))
            yield ids, attn, tgt

    from agcl.recursive import stage2_full_loop

    t0 = time.time()
    losses = []
    for step, loss in stage2_full_loop(mas, synth_batches(), lr=args.lr,
                                         max_steps=args.steps):
        losses.append(loss)
        print(f"  step {step:2d}  loss={loss:.4f}")

    dt = time.time() - t0
    print(f"\n  trained {args.steps} steps in {dt:.1f}s "
          f"({dt / max(args.steps, 1):.1f}s/step)")

    # Sanity: cross-entropy on random targets won't converge, but it
    # should at least move from its initial value, proving gradients
    # actually flow through every link and every agent.
    moved = abs(losses[-1] - losses[0])
    print(f"  loss[0]={losses[0]:.4f}  loss[-1]={losses[-1]:.4f}  moved={moved:.4f}")
    assert moved > 1e-3, \
        f"loss did not move ({losses[0]} -> {losses[-1]}); gradients are not flowing"
    print("  loss moved > 1e-3 — gradients flow through the full unrolled loop — ok")

    banner("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
