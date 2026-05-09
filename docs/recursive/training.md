# Training in RecursiveMAS — what actually happens, step by step

This is a friendly, beginner-friendly walkthrough of every training step
the recursive multi-agent system takes when you run a question through
it. If you've ever typed `python main.py recursive run` and seen a wall
of `[stage A] step 12/30 cos-loss=0.43` lines and wondered what they
mean — this doc is for you.

You don't need a machine-learning background to follow it. A loose grasp
of "vectors" and "loss" is enough.

> **Where you are:**  this is the deep-dive on training.
> Other useful docs:
> - **[guide.md](../guide.md)** — first-time setup (you are here if you've
>   already done the 15-minute beginner walkthrough)
> - **[configuration.md](../configuration.md)** — what every knob does
> - **[gui.md](../gui.md)** — node API for GUI integration
>   (the SSE events you stream to the GUI map directly to the steps
>   in this doc)
> - **[recursive/advanced.md](advanced.md)** — choosing models &
>   patterns
> - **[recursive/setup.md](setup.md)** — terse
>   technical reference

---

## The problem this solves (the "?" bug)

When you first run an untrained MAS:

```bash
$ python main.py recursive run "explain quantum entanglement"
[mas] running loop...
?
```

Just a `?`. That's not a bug in the loop — the loop is doing exactly
what it's supposed to. The reason you see `?` is:

1. The MAS is built from two language models: a **planner** (e.g.
   Qwen 0.5B, hidden size 896) and a **solver** (e.g. TinyLlama 1.1B,
   hidden size 2048).
2. Between them sit two tiny neural nets called **InnerLink** and
   **OuterLink**. They're 2-layer MLPs, freshly initialized (random
   weights).
3. The "OuterLink" between planner and solver is supposed to translate
   the planner's 896-dim "thought" into the solver's 2048-dim space.
   But at random init, that translation is just noise.
4. The solver receives this noise vector right before it starts
   generating, gets confused, emits `?`, and stops.

So we need to **train** those small projection MLPs so the latent
actually means something to the next agent. That's what this doc is
about.

---

## The three pieces

`RecursiveSession` (defined in `agcl/recursive/session.py`) does
three things in order on each conversation turn:

```
  user message
      │
      ▼
  ┌──────────────────┐
  │  topic gate      │  is this a new topic?
  │  (planner emb +  │
  │  cloud confirm)  │
  └────────┬─────────┘
           │
   new ────┼──── follow-up
           │              │
           ▼              ▼
  ┌──────────────────┐   ┌─────────────────┐
  │ resolve topic:   │   │ run local MAS   │
  │  retrieve cached │   │ (with cloud     │
  │  OR train fresh  │   │  fallback if    │
  └────────┬─────────┘   │  output is junk)│
           │             └─────────┬───────┘
           └──── answer ◄──────────┘
```

We'll walk through each box.

---

## Step 1 — the topic gate

Every turn, before doing anything else, the session asks: *is this a new
topic, or a follow-up to what we were just discussing?*

The gate has two layers:

### 1a. Embedding cosine drop (cheap, local)

The planner agent encodes your message into a vector (the last hidden
state of its forward pass). The session keeps a running **EMA centroid**
— a smoothed average of the embeddings from your recent turns.

```
turn N:    embed("what about its applications?")  ───►  vector v
                                                          │
                                                          │  cosine
                                                          ▼
   centroid (running average of recent turns)  ─────►  similarity
                                                          │
                                                          ▼
                                                  if > 0.6 : same topic
                                                  if < 0.6 : maybe switch
```

Cosine similarity ranges from `-1` (opposite) to `1` (identical
direction). Above `0.6` means "this turn is in the same neighborhood as
what we were just talking about." Below means it might be a switch.

This costs one forward pass through the planner — maybe 100 ms on a
laptop. Tunable via `--switch-threshold` (default `0.6`).

### 1b. Cloud confirmation (only when the gate fires)

When the gate says "maybe switch," the session sends one tiny prompt to
the cloud:

```
[A] previous turn
[B] new turn

Is [B] a topic switch from [A]? Reply yes/no.
```

This costs one cloud round-trip. It's there because the embedding gate
is jumpy — questions like "give me an example" are technically far in
embedding space from the seed but really are follow-ups.

If cloud says **no**, we proceed as a normal follow-up. If **yes**, we
move to step 2.

> **Why two layers?** The cheap gate fires often; the expensive
> confirmation only runs when the gate fires. So in steady state (most
> follow-ups), there are zero cloud calls — just one tokenizer pass.

---

## Step 2 — resolving a topic (retrieve or train)

So we've decided this is a new topic. Now what?

The session first tries **retrieval**. Maybe you've discussed something
similar before, and we already have trained link-weights saved on disk.

### 2a. Topic index lookup

Every time the session has trained on a topic, it saved:

```
.agent_state/mas_topics/<topic_id>/
    meta.json       # seed question, dim signature, timestamps
    centroid.pt     # planner-embedding of the seed (~896 floats)
    links.pt        # the trained inner+outer MLP weights
```

To check if we have a similar topic, we:

1. Embed the new user message with the planner.
2. Load every saved topic's centroid.
3. Filter to topics whose **dim signature** matches the current MAS
   (a 896+2048 setup can't load weights from a 1024+1024 setup — the
   shapes don't fit).
4. Take cosine similarity, find the best match.
5. If best match ≥ `--retrieval-threshold` (default `0.75`), we
   **load** that topic's `links.pt` directly into the running MAS. No
   training. No cloud call.

```
new question: "how does superposition work in qubits?"
                          │
                          ▼ planner embed
                          │
                          ▼ cosine search
       ┌──────────────────┴───────────────────┐
       │                                      │
   topic 1 (saved):                       topic 2 (saved):
   "explain quantum entanglement"        "what is a sql JOIN"
   sim = 0.81  ← match!                  sim = 0.04
       │
       ▼  load links.pt
   MAS now has trained weights for the quantum topic.
   no training needed.
```

Saved topics accumulate over the lifetime of the project. Returning to
a similar subject costs ~1 ms instead of ~1 minute of training.

### 2b. If nothing matches: bootstrap + train

When no saved topic crosses the threshold, the session **trains from
scratch on this topic**. This has three sub-steps. They're the same
three steps you see scrolling in the terminal:

```
[agcl] bootstrapping training from cloud...
[agcl] cloud answer: 412 chars, 6 reformulations
[agcl] training links (stage A: 30 steps, stage B: 20 steps)...
  [stage A]   1/30  cos-loss=0.9234
  [stage A]  10/30  cos-loss=0.4801
  [stage A]  20/30  cos-loss=0.1742
  [stage A]  30/30  cos-loss=0.0683
  [stage B]   1/20  ce=8.4123
  [stage B]   5/20  ce=6.7012
  [stage B]  10/20  ce=5.1408
  [stage B]  20/20  ce=3.0521
[agcl] persisted topic 92ff1629f157
```

The next three sections walk through each line.

---

## Step 3 — bootstrap (one cloud call)

The cloud model is asked **one** question. It returns a JSON object
with:

```json
{
  "answer": "Quantum entanglement is a phenomenon where two particles
             become correlated such that the state of one instantly
             influences the other...",
  "reformulations": [
    "What is quantum entanglement in plain words?",
    "How does entanglement between particles work?",
    "Can you describe quantum entanglement simply?",
    "What does it mean for two quantum particles to be entangled?",
    "Why is quantum entanglement strange?",
    "Briefly explain the idea of quantum entanglement."
  ]
}
```

The **answer** is what we want our local MAS to learn to produce. The
**reformulations** are different ways the user might ask the same
question — using them as training inputs prevents the MAS from
memorizing one exact wording and ignoring slight variations.

That's it for the cloud. From here, no more cloud calls until the next
topic.

> **What model is "cloud"?** Whatever you set as `DEFAULT_CLOUD`
> (`claude` by default, or `openai`). Per-session override:
> `--cloud-provider`.

---

## Step 4 — Stage A (latent alignment)

> **Goal:** make the loop's final "thought vector" point in the same
> direction as the answer's representation.

Here's what happens for **30 steps** (configurable with
`--stage1-steps`):

```python
for each step:
    text = pick_one(reformulations + [original_question])

    # forward pass through the WHOLE recursive loop
    final_latent = mas.run_latent_text(text)

    # what the final agent's encoding of the cloud answer looks like
    target_latent = final_agent.encode(cloud_answer)

    # how far off are we?
    loss = 1 - cosine_similarity(final_latent, target_latent)

    # nudge inner+outer link weights to reduce loss
    loss.backward()
    optimizer.step()
```

Two things to notice:

1. **Only the small MLPs train.** Both language models stay frozen —
   their billions of weights are never touched. The optimizer only sees
   the InnerLink + OuterLink params (a few hundred thousand
   floats total).
2. **The target is fixed across the whole stage.** It's just the cloud
   answer's hidden state, computed once. We're teaching the loop:
   "whatever input variant comes in, route it to *this* spot in latent
   space."

Visually:

```
      reformulation 0   ───►  loop  ───►  ●          (move toward)
      reformulation 1   ───►  loop  ───►  ●  ──►     ★  target_latent
      reformulation 2   ───►  loop  ───►  ●          (cloud answer
              ...                                     encoded)
```

The cosine-loss numbers you see drop from ~0.9 toward ~0.05 as the
links learn the right rotation/translation.

Stage A is fast because the only thing changing is the small MLPs. On
CPU with a 0.5B + 1.1B pair, each step is maybe 1-3 seconds.

---

## Step 5 — Stage B (token decoding)

> **Goal:** make the final agent actually produce the answer's words
> when fed the loop's latent.

Stage A made the latent line up correctly. But the final agent still
has to *decode* that latent into text. Stage B trains it to do that.

For **20 steps** (configurable with `--stage2-steps`):

```python
for each step:
    text = pick_one(reformulations + [original_question])

    # 1. run the loop, get the latent
    loop_latent = mas.run_latent_text(text)

    # 2. teacher-force: feed the model (prompt + answer)
    #    with the loop's latent injected at the boundary
    embeddings = embed(prompt_tokens) + [loop_latent] + embed(answer_tokens)

    # 3. forward through the final agent
    logits = final_agent.model(embeddings)

    # 4. cross-entropy loss on JUST the answer positions
    loss = cross_entropy(logits[answer_positions], answer_tokens)

    loss.backward()
    optimizer.step()
```

Stripped of detail, Stage B is saying: "given the loop's latent
appended right before where the answer should start, the final agent
should produce these specific tokens, in order."

Why does this matter? Because at **inference** time, the session calls
`decode_text` which does exactly the same thing: appends the loop
latent at the prompt boundary, then runs `model.generate`. So Stage B's
training matches the inference path one-to-one — what trains is what
runs.

The CE numbers you see going from ~8 down to ~3 means the model is
becoming more and more confident at picking the answer's tokens.

Stage B is slower than Stage A because we forward (prompt + answer)
not just (prompt). On CPU, each step is maybe 3-8 seconds.

> **Stage B is HF-only.** GGUF backends don't expose `inputs_embeds`,
> so we can't inject the latent at training time. If your **final**
> agent is GGUF, Stage B is auto-skipped — the message
> `[stage B] final agent is GGUF — skipped` will appear and you'll
> only get Stage A. The MAS still works, just less polished.

---

## Step 6 — persistence

After training finishes, we save three files to disk:

```
.agent_state/mas_topics/92ff1629f157/
    meta.json       (~400 bytes)
    centroid.pt     (~3.5 KB    — just the seed embedding)
    links.pt        (~50 MB     — depends on agent dim sizes)
```

The `topic_id` (`92ff1629f157`) is the first 12 chars of
`sha1(seed_question)`. This means asking the same question twice
overwrites the same directory instead of duplicating, and there's no
way two unrelated topics collide.

The `links.pt` only contains InnerLink + OuterLink weights — nothing
about the language models themselves. So even though TinyLlama is
1.1B parameters, we're only persisting the small projection MLPs
between agents. Hence the modest size.

---

## Step 7 — generation (steady-state turn)

Once a topic is loaded (either retrieved or freshly trained), every
follow-up runs through this path:

```
your message
     │
     ▼
mas.generate_text(message, max_new_tokens=128)
     │
     ▼
final agent's decode_text(prompt, injected=loop_latent)
     │
     ▼
text answer  ──►  print
```

If the answer comes out **degenerate** — too short (`< 8 chars`),
nothing but punctuation, or the same word repeated — the session
auto-falls back to the cloud and shows you that instead. This catches
the rare cases where the loop produces gibberish even after training,
without you having to know it happened.

You can also explicitly:

- Type `/cloud <message>` (interactive) — skip local entirely, use
  cloud directly.
- Run with `--continue-with-cloud` — local MAS produces a short prefix
  (like the main agent does), cloud finishes the rest.

---

## Tunables — what to change and when

Every knob below is on `python main.py recursive run`.

| Flag | Default | What it does | When to change |
|---|---|---|---|
| `--stage1-steps` | 30 | Stage A iterations | Increase if cos-loss isn't dropping below ~0.2; decrease for faster bootstrap if quality is fine |
| `--stage2-steps` | 20 | Stage B iterations | Increase for cleaner output (slower); set to 0 to disable Stage B entirely |
| `--switch-threshold` | 0.6 | When to flag a topic switch | Lower (0.4) if it switches too eagerly; raise (0.75) if it ignores real switches |
| `--retrieval-threshold` | 0.75 | When to reuse a saved topic | Raise (0.85) if retrieval reuses too aggressively; lower (0.6) for more aggressive reuse |
| `--n-reformulations` | 6 | Question paraphrases asked from cloud | More = more robust to wording changes, slower training |
| `--continue-with-cloud` | off | Use cloud as continuator like the main agent | Always-on for quality; off for full local |
| `--prefix-tokens` | 12 | How many tokens local makes before cloud takes over | Higher if the cloud feels like it's restarting; lower for more cloud control |
| `--no-persist` | off | Don't save trained links | Useful for one-off experiments |

---

## What is *not* training (and what it means)

Some things deliberately stay frozen, because their gradients would be
huge and not helpful:

- **The language models.** All planner/solver weights stay exactly as
  HuggingFace shipped them. Only the small projection MLPs between
  agents learn.
- **Tokenizers.** Each agent uses its own tokenizer; nothing changes
  there.

Some things are **not learned at all** — they're computed, not trained:

- **Topic centroid.** This is just the planner's encoding of the seed
  question. It's used for similarity, not for decoding.
- **Topic id.** Just a hash of the seed question.

So when you persist a topic, you're persisting roughly: "for this kind
of question, *this is the rotation/translation* between planner and
solver that produces a useful answer."

---

## Manual training (the older API)

The auto-training above wraps a lower-level API in
`agcl/recursive/train.py`. You can call those directly when you
want full control or you have your own dataset:

```python
from agcl.recursive import (
    build_from_config, stage1_warmup_inner, stage2_full_loop,
)

mas = build_from_config()

# Stage 1 (older API): per-agent inner-link warmup against a target.
def batches():
    for _ in range(100):
        yield (your_input_ids, your_attention_mask)

def target_provider(batch):
    return your_target_latent_for(batch)

for step, loss in stage1_warmup_inner(
    mas.agents[0], mas.inner[0], batches(), target_provider, max_steps=100,
):
    print(step, loss)

# Stage 2 (older API): full-loop CE on a real dataset.
def labeled_batches():
    for _ in range(500):
        yield (input_ids, attention_mask, target_ids)

for step, loss in stage2_full_loop(mas, labeled_batches(), lr=1e-4, max_steps=500):
    print(step, loss)
```

Use this when:
- You have real training data (not just a single cloud answer).
- You want to fine-tune the agent LMs themselves (set
  `freeze_agent=False` in stage1).
- You're running inside a notebook and want progress callbacks.

Use the auto-training (`RecursiveSession`) when:
- You want zero-setup. Type a question, it figures the rest out.
- You're running interactively.
- You want to accumulate a library of trained topics over time.

---

## Common questions

**"Why does the first turn take a minute and follow-ups are instant?"**
First turn = train from scratch. Follow-ups = trained weights are
already in memory; just one forward pass through the loop.

**"Why does it ask the cloud on the second message of a brand-new
conversation?"** Because at that point the gate fires (centroid is
brand new, so most things look "different") and the cloud confirms
it's not really a switch.

**"Can I disable the topic switch detection?"** Set
`--switch-threshold -1`. The cosine sim never drops below `-1`, so
the gate never fires and you stay on the same topic forever.

**"Will training mess up my models?"** No. The HF/GGUF model weights
are never touched. Only the projection MLPs change.

**"How big do `links.pt` files get?"** Roughly
`(N + 1) × max(d_i)² × 8 bytes`, where N is agent count and d_i are
hidden sizes. For Qwen 0.5B + TinyLlama 1.1B (896 + 2048), that's
~50 MB per topic. A few dozen topics is fine; thousands would be
noticeable.

**"Can I clear all saved topics?"**
`rm -rf .agent_state/mas_topics/`.

---

## Where to go next

- **[configuration.md](../configuration.md)** — every config knob and
  what it changes
- **[recursive/advanced.md](advanced.md)** — picking models and
  patterns
- **[recursive/setup.md](setup.md)** — the terse
  technical reference
- The actual code is small: see
  `agcl/recursive/auto_train.py`,
  `agcl/recursive/session.py`,
  `agcl/recursive/persistence.py`.
