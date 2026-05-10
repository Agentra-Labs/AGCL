# Troubleshooting

Common problems and the fastest way to fix each one.

> See also:
> - [docs/configuration.md](configuration.md) — every config knob
> - [docs/web-console.md](web-console.md) — UI-specific issues
> - [docs/recursive/training.md](recursive/training.md) — MAS auto-training
>   diagnostics

---

## Local model

### Local model outputs garbage or loops (`heiz heiz heiz...`)

Your model is either a base model (not instruction-tuned) or the
ChatML template doesn't match it.

```bash
export PROMPT_FORMAT=plain
```

The plain format uses `Q: … A:` instead of ChatML tags. If you still
see repetition, check that `repeat_penalty` is in effect — the server
console prints the raw local model output on each request.

### High latency on first request after idle

Expected. The local model is reloading. Subsequent requests in the same
active window are fast. Register a pattern trigger to pre-load the model
during your known active hours to avoid this — see *Pattern learning*
in [docs/configuration.md](configuration.md).

### Local prefix is wrong every time

Increase `PREFIX_WORD_COUNT` (default `4–6`). With a fast `instruct`
model, 6 words is usually fine. With a base model or a tiny one,
fewer words can be safer (less time to drift).

---

## Cloud / continuation

### Cloud model repeats the prefix instead of continuing

The continuation prompt tells the cloud to never repeat the prefix,
but some models ignore this when the prefix is very short. Increase
`PREFIX_WORD_COUNT` to 8–10 so the continuation point is unambiguous.

### Recovery mode looks rude / too apologetic

Three styles, switch via env or `--recovery`:

| Mode       | Behavior                                                         |
|------------|------------------------------------------------------------------|
| `natural`  | Pivots to the right answer without mentioning the mismatch (default) |
| `humor`    | Briefly and warmly acknowledges the mismatch before correcting    |
| `explicit` | Directly says the prefix was wrong, then answers                  |

```bash
python main.py --recovery natural
```

### `JSONDecodeError` in the CLI

You're on a very old `main.py`. Older versions used Python f-strings
to build JSON, which produces invalid output when text contains
quotes or special characters. Pull the latest — the fix is using
`json.dumps` for all SSE payloads.

---

## Recursive MAS

### Untrained MAS returns a single `?` token

Expected on first turn. `recursive run` triggers a one-shot cloud
teacher pass to bootstrap the inner / outer link weights. Wait for
`[stage A]` and `[stage B]` lines in the terminal — first turn takes
~1–3 minutes on CPU; subsequent turns on the same topic are local-only.

### Topic switches retrain even though the question is similar

Lower `--switch-threshold` (default `0.6`). Below this cosine
similarity, the session asks the cloud "is this the same topic?"
before retraining. Lower number = more permissive.

### Saved topic is never reused

Increase `--retrieval-threshold` (default `0.75`) only if topics are
being mistakenly reused; *lower* it (e.g. `0.65`) if related questions
aren't matching. Note: signatures with mismatched per-agent dims are
filtered automatically — `OuterLink` shapes are baked in.

### Stage B never runs

Stage B (token-CE) requires the **final** agent to be HF-backed. With a
GGUF final agent it's skipped — you only get Stage A (latent
alignment). To get Stage B, set the last agent in `mas.json` to
`backend: "hf"`.

---

## Web Console (`/web_socket/`)

### "Connect failed: Network error"

The node isn't running, or the host/port is wrong:

```bash
curl http://localhost:9876/node/health
# expect: {"ok": true, "service": "agcl-node", ...}
```

### "Saved key rejected"

The node was restarted and its in-memory key changed. Either:

```bash
# pin a static key
export AGCL_NODE_AUTH=$(openssl rand -hex 32)
python main.py node --port 9876
```

…or click **Disconnect** and paste the new key.

### CORS error in DevTools

Restart the node with the right allowlist:

```bash
python main.py node --port 9876 --cors "https://your-user.github.io"
```

### Mixed-content blocked (Firefox, GitHub Pages → HTTP localhost)

See [docs/web-console.md → Mixed-content note](web-console.md#mixed-content-note-https-site--http-localhost).

---

## Toolkit / gateways

### `toolkit ping ollama` says unreachable

```bash
curl ${OLLAMA_HOST:-http://localhost:11434}/api/tags
```

If that fails, Ollama isn't running. Start it (`ollama serve`) or
set `OLLAMA_HOST` to where it actually listens.

### `toolkit ping litellm` reports no models

You need to point the toolkit at a LiteLLM proxy, not the LiteLLM
Python library:

```bash
export AGCL_LLM_BASE_URL=http://localhost:4000   # litellm --port 4000
export AGCL_LLM_API_KEY=sk-...                   # whatever your proxy expects
```

### Manifest emit fails with "out_dir not allowed"

The server clamps `out_dir` to subpaths of `STATE_DIR` to prevent
arbitrary writes. Either leave `out_dir` blank (returns content
inline) or set it to something inside `.agent_state/`.

---

## Mini-trainer

### `enabled: no` and Start does nothing

`MINI_ENABLED=0` by default. Set it (Configuration tab → env vars,
or `.env` file), then restart the node OR click Start in the
Mini-Trainer tab.

### Loss never converges

Use Mini-Trainer → Presets → Strategies to pick a different training
strategy. The default one assumes a steady stream of latents from
running MAS turns; if you're idle, there's nothing to learn from.

### "requires rebuild" warning after editing config

Architecture-level fields (`arch`, `n_layers`, `n_heads`, vocab) need
the trainer to be torn down and rebuilt. Click Stop, then Start in
the Mini-Trainer tab.

---

## Sessions & state

### Sessions vanished after a restart

They're flushed to `STATE_DIR` (default `.agent_state/sess_*.json`).
If `STATE_DIR` is gitignored or on a tmpfs that gets cleared, that's
expected. Set `STATE_DIR=/var/lib/agcl` (or similar) for persistence
across reboots.

### Session is huge / context overflows

The system auto-recontextualizes when token count exceeds
`MAX_CONTEXT_TOKENS` (default `6000`). Tune `RECONTEX_KEEP_RECENT`
(default `6`) to control how many recent messages are kept verbatim
vs. summarized.

### Topic checkpoints take up disk

Each is one `meta.json` + `centroid.pt` + `links.pt` —
typically 1–4 MB. Delete unused ones from the Topics tab in the
console, or:

```bash
rm -rf .agent_state/mas_topics/<topic_id>
```

---

## Pattern learning

### `/patterns` is always empty

You haven't accumulated `MIN_PATTERN_DAYS` distinct days of activity
at any hour yet. Default is `3` — keep using AGCL and the count will
populate.

### Want to skip the warmup

Set `MIN_PATTERN_DAYS=1`. The pattern learner becomes more reactive
but also noisier — a single late-night accident registers as an
"active hour".
