# Slack AI Apps — Bolt for Python

<p align="left">
  <img src="../assets/slack.svg" width="22" alt="Slack" />&nbsp;
  <a href="https://img.shields.io/badge/Bolt-for%20Python-4A154B"><img alt="Bolt" src="https://img.shields.io/badge/Bolt-for%20Python-4A154B"></a>
</p>

The Slack adapter routes DMs into AGCL with one session per Slack user,
so each user keeps an independent recursive-MAS context.

> Part of the **[Agent-platform integrations](../integrations.md)**.

---

## Install

```bash
pip install slack-bolt              # HTTP mode
pip install slack-bolt[socket-mode] # Socket Mode (better for dev)
```

---

## Slack app config (do once in api.slack.com/apps)

| Required | Value |
|---|---|
| Bot Token Scopes  | `assistant:write`, `chat:write`, `channels:history`, `im:history`, `im:read` |
| Event subscriptions | `assistant_thread_started`, `assistant_thread_context_changed`, `message.im` |
| Features | Enable "Agents & AI Apps" |

---

## Run (Socket Mode, recommended for dev)

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
python main.py slack
```

## Run (HTTP mode, for production)

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_SIGNING_SECRET=...
python main.py slack --http --port 3000
```

---

## Behavior

- **Per-user session:** `session_id="slack-<user_id>"` — each Slack
  user has their own recursive-MAS context.
- **First-message greeting:** The first message in any thread triggers
  a `"AGCL ready..."` greeting via `assistant_thread_started`.
- **Tool registry:** All AGCL tools from
  [../../agcl/integrations/manifest.py](../../agcl/integrations/manifest.py)
  are reachable as commands; the default plain-text path goes through
  `agcl.mas.run`.
