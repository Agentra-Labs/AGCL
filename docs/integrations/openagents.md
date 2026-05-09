# OpenAgents (openagents.org) — AgentMod adapter

> Part of the **[Agent-platform integrations](../integrations.md)**.

The OpenAgents adapter publishes AGCL as an `AgentMod` so it can join
any OpenAgents workspace.

---

## Install

```bash
pip install openagents[sdk]
```

## Run

```bash
python main.py agentmod                          # register, don't join
python main.py agentmod --workspace ws-abc123    # join a workspace
```

---

## Routing

- `on_message` routes free-text into `agcl.mas.run`.
- `on_task` accepts `{"tool": "agcl.mini.test", "args": {...}}` shapes
  for explicit tool calls, falling back to a list of available tool
  names if an unknown id is requested.

---

## Programmatic use

To use the AGCL mod from your own OpenAgents network code:

```python
from agcl.integrations.openagents import AGCLMod

mod = AGCLMod()
mod.join("ws-abc123")
```
