# Recursive MAS — multi-agent reasoning in latent space

`agcl/recursive/` implements multi-agent reasoning that passes latent
embeddings between agents instead of text. Two small residual MLPs
(`InnerLink`, `OuterLink`) sit between the agents; the loop unrolls
*n* rounds and only the final agent decodes text.

This is the front door for the feature. Pick the depth you need:

| Doc | When to read |
|---|---|
| **[recursive/setup.md](recursive/setup.md)** | Terse technical reference — patterns, env vars, the build pipeline |
| **[recursive/training.md](recursive/training.md)** | What the auto-training loop actually does (the `[stage A]` / `[stage B]` lines you see in the terminal) |
| **[recursive/advanced.md](recursive/advanced.md)** | Picking models step-by-step, mixing HF + GGUF, manual training, all four patterns |

Adjacent topics:

- **[plugins.md](plugins.md)** — pluggable control surface (halt / pause / resume training, latent inspection) and the plugin contract for adding your own routes
- **[gui.md](gui.md)** — wiring the recursive runtime into a GUI / web frontend over HTTP+SSE
- **[configuration.md](configuration.md)** — every config knob in plain English

---

## At a glance

| Backend | Use | Notes |
|---|---|---|
| `hf` (HuggingFace transformers) | Full latent injection, full backprop | Required for Stage B (token-CE training) |
| `gguf` (llama.cpp) | Inference only | Final agent can be GGUF; Stage B auto-skips |

Patterns: `sequential` (planner → critic → solver), `moe`, `distill`
(teacher → student), `deliberation`, `custom`.

```bash
python main.py recursive validate    # 21 runnable checks
python main.py recursive info        # show resolved config
python main.py recursive run "..."   # one-shot
python main.py recursive run         # interactive multi-turn
```

Setup helpers:

```bash
python main.py --autoconfig          # one-shot canonical 2-agent HF setup
python main.py --config              # interactive wizard
```
