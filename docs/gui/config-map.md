# Editable-config map

Every knob the GUI can show + edit, with which env var it lives in,
whether changing it requires a rebuild, and the default.

> Part of the **[GUI integration guide](../gui.md)**.
> See also: [Endpoints](endpoints.md) — `GET/PATCH /node/config`.

---

## Persistent settings (`.env` / `mas.json`)

| GUI field | env var | Restart? | Default | Notes |
|---|---|:---:|---|---|
| **Cloud — provider** | `DEFAULT_CLOUD` | no | `claude` | `claude` or `openai` |
| **Cloud — OpenAI model** | `OPENAI_MODEL` | no | `gpt-4o` | |
| **Cloud — Claude model** | `CLAUDE_MODEL` | no | `claude-sonnet-4-20250514` | |
| **Cloud — OpenAI key** | `OPENAI_API_KEY` | yes | — | Edit via the GUI's secret store; the node only reports presence (`has_openai_key`) |
| **Cloud — Anthropic key** | `ANTHROPIC_API_KEY` | yes | — | Same |
| **Local model — path** | `LOCAL_MODEL_PATH` | yes | `models/SmolLM2-135M.Q2_K.gguf` | Path to a `.gguf` file |
| **Local model — n_ctx** | `LOCAL_N_CTX` | yes | `2048` | |
| **Local model — n_gpu_layers** | `LOCAL_N_GPU_LAYERS` | yes | `0` | `0` = pure CPU |
| **Local model — n_threads** | `LOCAL_N_THREADS` | yes | `12` | |
| **Local model — prefix words** | `PREFIX_WORD_COUNT` | no | `4` | Words local makes before cloud takes over |
| **MAS — pattern** | `MAS_PATTERN` | yes¹ | `sequential` | `sequential` / `moe` / `distill` / `deliberation` / `custom` |
| **MAS — rounds** | `MAS_ROUNDS` | yes¹ | `2` | Loop unroll count |
| **MAS — device** | `MAS_DEVICE` | yes¹ | `cpu` | Default device for HF agents |
| **MAS — dtype** | `MAS_DTYPE` | yes¹ | `float32` | Default dtype for HF agents |
| **MAS — agents** | `mas.json` | yes¹ | (canonical pair) | Edit via `PUT /node/config/mas` |
| **Storage — state dir** | `STATE_DIR` | yes | `.agent_state` | Where sessions + trained topics live |
| **Storage — idle flush** | `IDLE_FLUSH_SEC` | no | `180` | Seconds idle → flush + unload |
| **Storage — session TTL** | `SESSION_TTL_SEC` | no | `3600` | Seconds before evicting from RAM |
| **Storage — max context tokens** | `MAX_CONTEXT_TOKENS` | no | `6000` | Trigger compression at |

¹ MAS-shape changes can be applied without restarting the process by
calling `POST /node/mas/rebuild` after the patch.

---

## Per-call overrides

No env var; pass on each `POST /node/mas/run` or `/mas/stream`:

| GUI field | Body field | Default | Effect |
|---|---|---|---|
| Switch threshold | `switch_threshold` | `0.6` | Cosine sim below = candidate switch |
| Retrieval threshold | `retrieval_threshold` | `0.75` | Cosine sim above = reuse saved topic |
| Stage A steps | `stage1_steps` | `30` | Latent-alignment iterations |
| Stage B steps | `stage2_steps` | `20` | Token-CE iterations (0 disables) |
| Reformulations | `n_reformulations` | `6` | Question paraphrases asked from cloud |
| Max new tokens | `max_new_tokens` | `128` | Local generation length |
| Cloud continue | `cloud_continue` | `false` | Local prefix + cloud finish |
| Prefix tokens | `prefix_tokens` | `12` | Tokens of local prefix in continuator mode |
| Persist | `persist` | `true` | Save trained links to disk |
| Provider | `cloud_provider` | (default) | Per-session override of `DEFAULT_CLOUD` |
| Force cloud (per turn) | `force_cloud` | `false` | Skip local for this turn only |
| Force continue (per turn) | `force_continue` | `false` | Use continuator for this turn only |

> Session-defaults fields (`switch_threshold` through `persist`) are
> **sticky on the session**. They're only honored when the session is
> first created. To change them later, delete the session via
> `DELETE /node/mas/sessions/{sid}` and recreate it.
