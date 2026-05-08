# RecursiveMAS — full setup guide

This guide walks through configuring and running the recursive multi-agent
system from scratch. It covers the two supported model backends (HF and
GGUF), how to mix them, the four collaboration patterns, and what each
piece of `config.py` controls.

If you only want to confirm the implementation works, skip to the bottom:

    python main.py recursive validate    # 21 runnable checks

> **Where you are:** the dense technical reference.
> Friendlier docs:
> - **[guide.md](guide.md)** — first-time setup
> - **[configuration.md](configuration.md)** — config explained for
>   beginners
> - **[advanced_guide.md](advanced_guide.md)** — picking models,
>   patterns, training
> - **[training.md](training.md)** — what auto-training does, step
>   by step
> - **[integration.md](integration.md)** — node API for GUI integration
> - **[main.md](main.md)** — per-file code reference


## 1. What it does

Instead of agents passing text between each other every round, the
recursive MAS keeps reasoning in embedding space and only decodes text
once at the very end. Two small projection MLPs sit between the agents:

    InnerLink:  same-agent latent recurrence    R_in(h) = h + W2 GELU(W1 h)
    OuterLink:  cross-agent transfer            R_out(h) = W3 h + W2 GELU(W1 h)

The loop unrolls `n_rounds` times across all agents. Only the last agent
calls `.decode()` to produce text.


## 2. Backends

| Backend     | Library            | Latent injection | Training |
|-------------|--------------------|:----------------:|:--------:|
| `hf`        | transformers + torch | yes (`inputs_embeds`) | yes |
| `gguf`      | llama-cpp-python   | no (silently dropped at this agent's input) | no |

GGUF agents still **participate in the loop** — their last-token
embedding flows out through the OuterLink to the next agent. They just
can't condition on a previously injected latent at their own input,
because llama.cpp has no `inputs_embeds` equivalent. Mixed chains like
`gguf -> hf -> hf` are fine; pure-GGUF chains work for inference too,
they just lose the latent-injection step inside each agent.

If you want training (stage-1 warmup or stage-2 full loop), every agent
must be on the `hf` backend.


## 3. Install

The base package only needs `torch`. If you want HF agents, add
`transformers`. GGUF support comes via `llama-cpp-python`, which is
already a project dependency.

    pip install -r requirements.txt          # gives you torch + llama-cpp-python
    pip install transformers                 # only if you want hf agents


## 4. Configure agents

Three ways to declare agents (checked in order):

### A. `MAS_AGENTS` env var (JSON array)

    export MAS_AGENTS='[
      {"backend": "hf",   "model": "HuggingFaceTB/SmolLM2-135M", "role": "planner"},
      {"backend": "gguf", "model": "models/SmolLM2-135M.Q2_K.gguf", "role": "solver"}
    ]'

### B. `MAS_CONFIG_FILE` env var (path to JSON file)

    export MAS_CONFIG_FILE=./mas.json

`mas.json`:

    {
      "agents": [
        {"backend": "hf",   "model": "HuggingFaceTB/SmolLM2-135M", "role": "planner",
         "device": "cpu", "dtype": "float32"},
        {"backend": "gguf", "model": "models/SmolLM2-135M.Q2_K.gguf", "role": "critic",
         "n_ctx": 2048, "n_threads": 4, "n_gpu_layers": 0},
        {"backend": "hf",   "model": "HuggingFaceTB/SmolLM2-135M", "role": "solver"}
      ]
    }

### C. Default (when neither env var is set)

Two GGUF agents pointing at `LOCAL_MODEL_PATH` (the same gguf you use
for the cloud-prefix feature). Useful as a smoke test.


### Spec fields

Every agent spec is a dict:

| field        | required | applies to | meaning                                |
|--------------|:--------:|:----------:|----------------------------------------|
| `backend`    | yes      | both       | `"hf"` or `"gguf"`                     |
| `model`      | yes      | both       | HF id, local HF dir, or `.gguf` path   |
| `role`       | no       | both       | label only (used by patterns/info)     |
| `dtype`      | no       | hf         | `float32` / `float16` / `bfloat16`     |
| `device`     | no       | hf         | `cpu` / `cuda` / `cuda:0` / ...        |
| `n_ctx`      | no       | gguf       | context window (default 2048)          |
| `n_gpu_layers` | no     | gguf       | layers to offload to GPU (default 0)   |
| `n_threads`  | no       | gguf       | CPU threads (default 4)                |


## 5. Pick a pattern

Set `MAS_PATTERN` (env var). The loop wiring is the same across all
patterns — only the agent ordering / count expectations differ.

| pattern        | agents needed | typical use                              |
|----------------|---------------|------------------------------------------|
| `sequential`   | 1+            | planner → critic → solver                |
| `moe`          | 1+            | several specialized experts              |
| `distill`      | exactly 2     | teacher → student                        |
| `deliberation` | 2+            | reflector + tool-caller, ≥ 3 rounds      |
| `custom`       | 1+            | use whatever order you specified         |


## 6. Other config knobs

    MAS_ROUNDS    int, default 2     # number of recursion rounds
    MAS_DEVICE    str, default cpu   # default device for hf agents that don't set their own
    MAS_DTYPE     str, default float32  # default dtype for hf agents


## 7. Run it

    python main.py recursive info                    # show resolved config
    python main.py recursive run "your prompt here"
    python main.py recursive run "..." --max-new-tokens 64

Or in Python:

    from agcl.recursive import build_from_config
    mas = build_from_config()
    print(mas.generate_text("explain backprop in one sentence"))


## 8. Build a MAS programmatically

If you don't want to go through env vars / config:

    from agcl.recursive import RecursiveAgent, RecursiveMAS

    a1 = RecursiveAgent.from_pretrained("HuggingFaceTB/SmolLM2-135M", role="planner")
    a2 = RecursiveAgent.from_gguf("models/SmolLM2-135M.Q2_K.gguf", role="critic")
    a3 = RecursiveAgent.from_pretrained("HuggingFaceTB/SmolLM2-135M", role="solver")

    mas = RecursiveMAS([a1, a2, a3], n_rounds=2)
    print(mas.generate_text("solve x + 2 = 5"))

Or from explicit specs:

    from agcl.recursive import build_mas_from_specs
    mas = build_mas_from_specs(
        specs=[
            {"backend": "hf",   "model": "HuggingFaceTB/SmolLM2-135M", "role": "planner"},
            {"backend": "gguf", "model": "models/SmolLM2-135M.Q2_K.gguf", "role": "solver"},
        ],
        n_rounds=2,
        pattern="sequential",
    )


## 9. Training (HF only)

Two APIs:

### A. Manual training (offline, your own data)

    from agcl.recursive import stage1_warmup_inner, stage2_full_loop

Stage 1 — warm up each agent's InnerLink against a target embedding using
cosine-similarity loss. Underlying LM is frozen by default
(`freeze_agent=True`). Useful before unrolling the full loop.

Stage 2 — backprop cross-entropy on the final logits across the entire
unrolled MAS. All inner/outer links + agent params receive gradient.

Both helpers are step-by-step generators — the caller decides how to log
or stop. See [validate.py](../agcl/recursive/validate.py)
`t_stage1_warmup_reduces_loss` and `t_stage2_full_loop_reduces_loss` for
runnable examples on tiny synthetic data.

### B. Auto-training via cloud teacher (online, interactive)

    from agcl.recursive import auto_train, bootstrap_pairs

`auto_train(mas, question, answer, reformulations, ...)` runs a two-stage
inline trainer:

- **Stage A** — cosine-aligns the loop's final latent with the final
  agent's encoding of `answer`. Trains InnerLink + OuterLink only;
  agents are auto-frozen via `requires_grad=False`.
- **Stage B** — teacher-forces CE on `answer` tokens through the final
  HF agent with the loop-produced latent injected at the prompt/answer
  boundary. Auto-skipped if the final agent is GGUF.

`bootstrap_pairs(question)` calls cloud once and returns
`(answer, [reformulations])` parsed from JSON.

`RecursiveSession` (in `agcl/recursive/session.py`) wires these
together with topic detection and persistence — see section 12.

For a step-by-step explanation of what these stages are doing, see
**[training.md](training.md)**.


## 10. Validate

The full implementation is covered by 21 runnable checks:

    python main.py recursive validate

The suite covers:

- InnerLink/OuterLink residual identity at init, param counts, shape
  mapping, training reduces the loss
- Loop runs end-to-end with mixed agent dimensions
- Latent actually evolves across rounds (not stuck at identity)
- Stage-1 warmup reduces cosine-sim loss
- Stage-2 unrolled training is differentiable
- All four collaboration patterns build a working MAS
- HF/GGUF backends declare the required interface
- Loop respects `supports_injection()` (drops injection at gguf agents)
- Builder validates backend names and required fields
- Pattern dispatch instantiates the right agent count
- `config.MAS_AGENTS` / pattern / rounds parse cleanly

Exits 0 on all-pass, 1 if anything fails.


## 11. Troubleshooting

**"HFBackend requires transformers"** — pip install transformers.

**"GGUFBackend requires llama-cpp-python"** — already in
`requirements.txt`; reinstall it.

**The output is gibberish for tiny models** — RecursiveMAS doesn't fix
underlying model quality. SmolLM2-135M is enough for a smoke test, not
for real reasoning. Use 1B+ models for anything serious.

**Latents have wildly different scales across agents** — train stage 1
first to align the latent space; OuterLink alone won't fix it.

**OOM with multiple HF agents** — each spec loads its own copy of the
weights. Use `dtype: "float16"` or `bfloat16`, or move some agents to
the gguf backend (quantized + cheap).

**"unknown pattern"** — must be one of
`sequential | moe | distill | deliberation | custom`.


## 12. Multi-turn session, persistence, retrieval

`RecursiveSession` is the multi-turn driver around `RecursiveMAS`. It
adds three things on top of the bare loop:

### Topic gate

Each user turn is embedded by the planner agent and compared (cosine)
to a running EMA centroid. A drop below `switch_threshold` (default
0.6) triggers a yes/no cloud confirmation; if confirmed, the session
treats the turn as a topic switch.

### Persistence + retrieval

After successful auto-training, the session writes:

    STATE_DIR/mas_topics/<topic_id>/
        meta.json       seed question, dim signature, timestamps
        centroid.pt     planner-embedding of the seed
        links.pt        state_dict of mas.inner + mas.outer

`<topic_id>` = first 12 chars of `sha1(seed_question)`.

On the next topic switch, before retraining, the session embeds the
new seed and finds the highest-cosine saved topic whose dim signature
matches the running MAS. If the match is above
`retrieval_threshold` (default 0.75), the saved `links.pt` is loaded
and training is skipped. This keeps recurring subjects fast — one
cosine sim + one `torch.load` instead of a 1-3 min training pass.

### Cloud continuator mode

`cloud_continue=True` makes the local MAS produce only `prefix_tokens`
tokens (default 12), then `cloud.stream_continuation` finishes from
that open assistant turn. Mirrors the main agent's local→cloud
handoff. Per-turn override: pass `force_continue=True` to `.turn()`.

### Programmatic use

    import asyncio
    from agcl.recursive import build_from_config, RecursiveSession

    mas = build_from_config()
    sess = RecursiveSession(
        mas,
        switch_threshold=0.6,
        retrieval_threshold=0.75,
        stage1_steps=30, stage2_steps=20,
        cloud_continue=False,
        persist=True,
    )

    out = asyncio.run(sess.turn("explain quantum entanglement"))
    print(out)

    out2 = asyncio.run(sess.turn("how does it relate to qubits?"))
    print(out2)

The relevant helpers are exported at the package root:

    from agcl.recursive import (
        RecursiveSession, auto_train, bootstrap_pairs,
        cloud_confirm_switch, TopicTracker, persistence,
    )
    persistence.list_topics(state_dir)
    persistence.find_similar_topic(state_dir, query_centroid, threshold=0.75)
    persistence.save_topic(state_dir, mas, seed, centroid)
    persistence.load_topic(state_dir, mas, topic_id)
    persistence.delete_topic(state_dir, topic_id)


## 13. Configurator

For interactive setup, `python main.py --config` launches a
prompt-based wizard that walks through pattern, agent count, per-agent
backend, model id/path, role, dtype/device. Optionally, every HF
model can be fully downloaded into `models/hf_local/<slug>/` so
subsequent runs do not hit the HF Hub at all. The wizard writes
`mas.json` and patches `.env`.

Source: `agcl/configurator.py`.

For a guided walkthrough see
[configuration.md](configuration.md#path-2-interactive-wizard---config).
