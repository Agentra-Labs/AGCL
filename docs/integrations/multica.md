# Multica.ai integration

Multica (https://github.com/multica-ai/multica) is a Linear-style task
control plane. It does **not** call LLMs. It assigns issues to whichever
agent CLI a user has on PATH, and a local daemon routes issues into
that CLI. From AGCL's side, the integration is being a well-behaved
JSONL-emitting CLI plus a small plugin that exposes the same surface
over HTTP for live inspection.

> Part of the **[Agent-platform integrations](../integrations.md)**.

---

## What ships in AGCL today

| Piece | Path | Status |
|---|---|---|
| `agcl run --output jsonl` runner | [`agcl/runner.py`](../../agcl/runner.py) | works |
| Multica plugin (HTTP routes + tool) | [`plugins/multica.py`](../../plugins/multica.py) | works (auto-loaded if file present) |
| Skill ctx reader (`<workdir>/.agcl/context.md` and `.agcl/skills/*.md`) | plugin | works |
| Session resume via `--session-id` (= Multica's SessionID) | runner | works |
| Go backend implementation (lives in Multica's repo) | external | not in this repo — see "Go-side wiring" below |

The Go file (`server/pkg/agent/agcl/agcl.go` in the multica-ai/multica
repo) is what you fork and add. It calls `agcl run --output jsonl`,
parses the lines, and translates them into Multica's internal Event
struct. AGCL's responsibility ends at emitting the right lines.

---

## Wire format AGCL emits (what your Go backend reads)

```jsonl
{"type": "status",      "message": "starting", "session_id": "...", "started_at": 1715200000.0}
{"type": "status",      "message": "building MAS"}
{"type": "thinking",    "stage": "stage1", "content": "step 12/30 loss=0.0631"}
{"type": "thinking",    "stage": "stage2", "content": "step 8/20 loss=3.41"}
{"type": "tool_call",   "name": "...", "input": {...}}    // emitted by tools/plugins
{"type": "tool_result", "name": "...", "output": "..."}
{"type": "text",        "content": "first words of the answer..."}
{"type": "answer",      "content": "the full answer", "topic_id": "...", "session_id": "..."}
{"type": "error",       "message": "...", "etype": "RuntimeError"}    // only on failure
{"type": "done",        "status": "completed|failed|cancelled", "session_id": "...", "topic_id": "...", "elapsed_sec": 12.3}
```

The Multica Go code maps this onto its own `EventType` enum exactly as
you'd expect:

```go
case "text":        return agent.Event{Type: agent.EventText,       Content: ...}
case "thinking":    return agent.Event{Type: agent.EventThinking,   Content: ...}
case "tool_call":   return agent.Event{Type: agent.EventToolUse,    Content: ..., Meta: raw}
case "tool_result": return agent.Event{Type: agent.EventToolResult, Content: ...}
case "status":      return agent.Event{Type: agent.EventStatus,     Content: ...}
case "error":       return agent.Event{Type: agent.EventError,      Content: ...}
default:            return agent.Event{Type: agent.EventLog,        Content: fmt.Sprintf(...)}
```

`done` is read separately to decide success/failure status.

---

## Files you change in the Multica fork

| File | Change |
|---|---|
| `server/pkg/agent/agent.go` | Add `case "agcl": return agcl.New(cfg)` to `New()` |
| `server/pkg/agent/agcl/agcl.go` | **Create this** — `Backend` impl that runs `agcl run --output jsonl` |
| `server/internal/daemon/config.go` | Add `"agcl"` to the `knownProviders` probe list |
| `server/internal/execenv/context.go` | Add `case "agcl": return writeAGCLSkillContext(workDir, skills)` |

`writeAGCLSkillContext` should write `<workdir>/.agcl/context.md` —
that's where AGCL's plugin reads injected skills from.

When [multica-ai/multica#257](https://github.com/multica-ai/multica/issues/257)
adds plugin registration, the daemon and factory edits go away; only
the `agcl/` package + skill writer remain.

---

## Reference Backend.Execute (Go)

```go
package agcl

import (
    "bufio"
    "context"
    "encoding/json"
    "fmt"
    "os/exec"

    "github.com/multica-ai/multica/server/pkg/agent"
)

type Backend struct{ cfg agent.Config }

func New(cfg agent.Config) (*Backend, error) { return &Backend{cfg: cfg}, nil }

func (b *Backend) Execute(ctx context.Context, task agent.Task, events chan<- agent.Event) error {
    args := []string{
        "run",
        "--task",   task.Description,
        "--workdir", task.WorkDir,
        "--output", "jsonl",
    }
    if task.SessionID != "" {
        args = append(args, "--session-id", task.SessionID)
    }
    cmd := exec.CommandContext(ctx, "agcl", args...)
    cmd.Dir  = task.WorkDir
    cmd.Env  = append(cmd.Env, envSlice(task.Env)...)

    stdout, err := cmd.StdoutPipe()
    if err != nil { return err }
    if err := cmd.Start(); err != nil { return err }

    scanner := bufio.NewScanner(stdout)
    scanner.Buffer(make([]byte, 1024*1024), 8*1024*1024) // long lines
    for scanner.Scan() {
        var raw map[string]any
        if err := json.Unmarshal(scanner.Bytes(), &raw); err != nil {
            events <- agent.Event{Type: agent.EventLog, Content: scanner.Text()}
            continue
        }
        events <- translate(raw)
    }
    return cmd.Wait()
}

func translate(raw map[string]any) agent.Event {
    s := func(k string) string { v, _ := raw[k].(string); return v }
    switch raw["type"] {
    case "text":        return agent.Event{Type: agent.EventText,       Content: s("content")}
    case "thinking":    return agent.Event{Type: agent.EventThinking,   Content: s("content"), Meta: raw}
    case "tool_call":   return agent.Event{Type: agent.EventToolUse,    Content: s("name"),    Meta: raw}
    case "tool_result": return agent.Event{Type: agent.EventToolResult, Content: s("output")}
    case "status":      return agent.Event{Type: agent.EventStatus,     Content: s("message")}
    case "error":       return agent.Event{Type: agent.EventError,      Content: s("message")}
    case "answer":      return agent.Event{Type: agent.EventText,       Content: s("content"), Meta: raw}
    case "done":        return agent.Event{Type: agent.EventStatus,     Content: "done: " + s("status")}
    default:            return agent.Event{Type: agent.EventLog,        Content: fmt.Sprintf("%v", raw)}
    }
}
```

---

## Skill injection

Multica fetches skills server-side and the daemon writes them to disk
before invoking your backend. AGCL reads them from one of two layouts
(your `writeAGCLSkillContext` can use either):

```
<workdir>/.agcl/context.md            # one bundled file
<workdir>/.agcl/skills/<name>.md      # one file per skill (sorted, all loaded)
```

The Multica plugin's `GET /node/multica/skills?workdir=<dir>` returns
the parsed skill list so the Multica UI can show what AGCL is reading.

---

## What the plugin gives you (over and above the CLI)

When AGCL is running as a node, [`plugins/multica.py`](../../plugins/multica.py)
adds:

| Endpoint | Purpose |
|---|---|
| `GET  /node/multica/health` | `{"agcl": "ready", "session_dir": "...", ...}` — pre-flight before assigning tasks |
| `GET  /node/multica/skills?workdir=<path>` | Read injected skills under a workdir without invoking the LLM |
| `POST /node/multica/run` | Run a task body (`{description, workdir, session_id, skills, env}`) and stream the same JSONL as the CLI, served as SSE |

The plugin also calls `ctx.declare_config()` so the running AGCL's
`/node/plugins` endpoint surfaces the env vars Multica expects:

```
MULTICA_SERVER_URL    daemon endpoint (ws://localhost:8080/ws)
MULTICA_TOKEN         Personal Access Token (Bearer; secret)
MULTICA_WORKSPACE_ID  workspace UUID
```

The plugin also registers a tool `agcl.multica.run` so Multica-style
runs are reachable through MCP / Slack / OpenAgents — same code path,
no extra wiring.

---

## Quick checklist

- [ ] `agcl` binary on `$PATH` (or wrap with a shim that runs `python /path/to/AGCL/main.py "$@"`)
- [ ] Multica daemon detects AGCL — check `multica daemon logs -f`
- [ ] Add `case "agcl"` to Multica's `agent.New()` factory
- [ ] Implement `writeAGCLSkillContext` in `execenv/context.go`
- [ ] Issue assigned to AGCL goes from `in_progress` → `completed`
- [ ] `done.status: cancelled` flows back to Multica when the task is killed (Multica cancels `ctx`; the runner exits)
