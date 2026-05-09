# SSE event reference

`POST /node/mas/stream` returns a stream of `data: {…}\n\n` lines. The
GUI should parse each event as JSON and dispatch on `event`.

> Part of the **[GUI integration guide](../gui.md)**.
> See also: [Endpoints](endpoints.md) · [TypeScript client](typescript-client.md)

---

## Event reference

| `event` | Fields | When | What the GUI should do |
|---|---|---|---|
| `start` | `session_id` | first event | Open a "thinking" state |
| `turn_start` | `force_cloud`, `force_continue` | once per turn | — |
| `topic_gate` | `sim`, `threshold`, `candidate` | only on follow-ups | Optional debug indicator |
| `topic_switch_confirmed` | `switched` (bool) | when gate fires | Show "topic switch detected" badge |
| `retrieval_lookup` | — | new topic | "Looking up similar past topics…" |
| `retrieval_hit` | `topic_id`, `sim`, `seed` | found a saved topic | "Reusing trained topic from … (sim=0.81)" |
| `retrieval_miss` | `threshold` | no match | — |
| `bootstrap_start` | `is_switch` | begin training | "Asking cloud for a teacher answer…" |
| `bootstrap_done` | `answer_chars`, `n_reformulations` | cloud done | "Training on answer (412 chars)" |
| `stage1_start` | `total_steps` | begin Stage A | Begin progress bar |
| `stage1_step` | `step`, `total_steps`, `loss` | every step | Update Stage A progress + loss line |
| `stage1_done` | `first_loss`, `final_loss` | end Stage A | "Stage A: 0.92 -> 0.07" |
| `stage2_start` | `total_steps` | begin Stage B | Begin Stage B progress bar |
| `stage2_step` | `step`, `total_steps`, `loss` | every step | Update Stage B progress |
| `stage2_done` | `first_loss`, `final_loss` | end Stage B | "Stage B: 8.4 -> 3.1" |
| `stage2_skipped` | `reason` | GGUF or short answer | "Stage B skipped: GGUF final agent" |
| `topic_persisted` | `topic_id` | after training | "Saved topic …" |
| `topic_persist_failed` | `error` | save failed | Toast warning |
| `generation_start` | `mode` (`local`/`continuator`/`cloud_only`) | begin output | Switch to "writing" state |
| `prefix` | `text` | continuator mode | Display the local prefix |
| `prefix_dropped` | `prefix` | continuator mode, prefix degenerate | "Local prefix discarded" |
| `fallback_to_cloud` | `local_output` | local was junk | Toast "fell back to cloud" |
| `cloud_only_start` / `cloud_only_done` | `chars` | force_cloud | — |
| `generation_done` | `mode`, `chars` | local done | — |
| `answer_ready` | `chars` | bootstrap path | Cloud answer became the response |
| `answer` | `text`, `topic_id` | always | **The final answer string** |
| `error` | `type`, `message` | on exception | Show error banner |
| `done` | — | last event | Close the stream UI |

---

## Event order

The order is roughly:

```
start
  -> turn_start
  -> (topic gate)?
  -> (retrieval | bootstrap+training)?
  -> generation_start -> ... -> answer
  -> done
```

The first turn of a brand-new topic emits **all** of `bootstrap_*`,
`stage1_*`, `stage2_*`, `topic_persisted`, `answer`, `done` —
that's the slow path (~1–3 min on CPU). Follow-ups skip everything
between `turn_start` and `generation_start`.

---

## Training-control events

When the GUI calls `pause` / `resume` / `halt` against a session, the
`/mas/stream` it's already subscribed to will emit corresponding
events. See [../plugins.md](../plugins.md) for the exact payload
shapes:

- `paused`
- `resumed`
- `halted`
- `halted_done`
