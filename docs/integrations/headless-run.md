# Headless task runner — `agcl run`

`python main.py run` is the contract every external orchestrator
(Multica, Cloud Run jobs, GitHub Actions, plain shell scripts) uses to
drive AGCL.

> Part of the **[Agent-platform integrations](../integrations.md)**.

---

## Invocation

```bash
python main.py run \
  --task "summarize the latest commit" \
  --workdir /tmp/job-42 \
  --session-id job-42 \
  --output jsonl
```

Flags:

| Flag | Default | What it does |
|---|---|---|
| `--task` *(required)* | — | Task description / issue body / prompt |
| `--workdir` | cwd | Sets `STATE_DIR=<workdir>/.agcl` so each run is isolated |
| `--output` | `jsonl` | `jsonl` (default), `sse` (`data: {...}\n\n`), or `text` (legacy) |
| `--session-id` | `default` | Same id reuses prior `.agcl/sess_*.json` and topic links |
| `--resume` | — | Alias for `--session-id` (matches Multica's terminology) |
| `--provider` | env `DEFAULT_CLOUD` | `openai` or `claude` |
| `--cloud` | off | Skip the local recursive MAS for this turn |
| `--continue-with-cloud` | off | Local prefix + cloud finishes |
| `--skill <path>` | — | Repeatable. Markdown bundles prepended as context. |
| `--max-new-tokens` | `256` | Local generation length |

Exit code: `0` if `done.status == "completed"`, non-zero otherwise.

---

## Event taxonomy (jsonl mode)

One JSON object per line on stdout:

| `type` | Fields | When |
|---|---|---|
| `status` | `message`, optional `meta` | start, MAS build, retrieval hit/miss, generation start, persisted |
| `thinking` | `stage` (`stage1`/`stage2`/`topic-gate`/`gen`), `content` | training step + topic gate detail |
| `tool_call` | `name`, `input` | when a tool is invoked (plugin-emitted) |
| `tool_result` | `name`, `output` | tool returned |
| `text` | `content` | local prefix in continuator mode |
| `answer` | `content`, `topic_id`, `session_id` | full final answer (always emitted) |
| `error` | `message`, `etype` | only on failure |
| `done` | `status` (`completed`/`failed`/`cancelled`), `session_id`, `topic_id`, `elapsed_sec` | always last |

---

## Skill files

Multica injects skills by writing markdown files into the workdir before
calling AGCL. The runner reads them when you pass `--skill`:

```bash
python main.py run \
  --task "implement the spec" \
  --workdir /tmp/issue-1 \
  --skill /tmp/issue-1/.agcl/context.md \
  --skill /tmp/issue-1/.agcl/skills/code-style.md
```

Each `--skill` file is read as-is and joined with the next via
`\n\n---\n\n`, then the resulting block is prepended to the actual
task with one more `\n\n---\n\n` separator. No headings are added;
whatever you put in the file is what reaches the model. (If you want
`## name` headings you can add them inside the file, or use the
plugin's HTTP path — see below.)

The Multica plugin's `POST /node/multica/run` accepts inline skills
in the request body — those *do* get a `## <name>` heading prepended
because the names come from the JSON payload rather than filenames.
The plugin's `GET /node/multica/skills?workdir=<path>` returns the
same on-disk layout over HTTP for inspection.

---

## Wiring into shell scripts

```bash
#!/usr/bin/env bash
# trigger an AGCL run and pipe the events into your log pipeline
python main.py run --task "$1" --workdir "$WORK" --output jsonl \
  | tee "$WORK/agcl.jsonl" \
  | jq -r 'select(.type=="answer") | .content'
```

Or with GitHub Actions:

```yaml
- name: AGCL run
  run: |
    python main.py run \
      --task "${{ github.event.issue.body }}" \
      --workdir ${{ runner.temp }}/agcl-${{ github.run_id }} \
      --session-id "issue-${{ github.event.issue.number }}" \
      --output jsonl > agcl.jsonl
- uses: actions/upload-artifact@v4
  with:
    path: agcl.jsonl
```

Or in a Cloud Run Job:

```bash
gcloud run jobs create agcl-job \
  --image=gcr.io/$PROJECT/agcl:latest \
  --command="python" \
  --args="main.py,run,--task,SUMMARIZE_PR_TASK,--workdir,/tmp/job,--output,jsonl"
```

---

## Live HTTP equivalent

When AGCL is running as a node (`python main.py node`), the same
event stream is reachable as SSE:

```
POST /node/mas/stream
Authorization: Bearer <key>
Content-Type: application/json

{"message": "...", "session_id": "..."}
```

The events are the same shapes but namespaced with `event:` instead of
`type:` (legacy reasons). The Multica plugin re-emits them as the
unified `type:` taxonomy at `POST /node/multica/run`. Use whichever
matches your ingestor.
