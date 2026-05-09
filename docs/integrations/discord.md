# Discord adapter

<p align="left">
  <img src="../assets/discord.svg" width="22" alt="Discord" />&nbsp;
  <a href="https://img.shields.io/badge/discord.py-2.x-5865F2"><img alt="discord.py" src="https://img.shields.io/badge/discord.py-2.x-5865F2"></a>
</p>

AGCL ships a Discord adapter that routes DMs and `@mentions` into the
recursive MAS, plus a `/ask` slash command. Source lives at
[`agcl/toolkit/discord.py`](../../agcl/toolkit/discord.py).

> Part of the **[Agent-platform integrations](../integrations.md)**.

---

## Install + run

```bash
pip install discord.py            # gateway client (gives /ask + on_message)
export DISCORD_BOT_TOKEN=Bot.MTk4...
python main.py toolkit discord
```

Without `discord.py` installed, the gateway path prints a hint and
exits — but the **REST helpers** (post-to-channel, whoami) still work
because they hit the Discord HTTP API directly via `httpx`.

---

## What the adapter does

Routes each user to a per-author session id (`discord-<user_id>`), so
each Discord user keeps an independent recursive-MAS context. Every
message goes through the manifest tool `agcl.mas.run`, which means
plugins/tools registered there are available without further wiring.

| Hook | Behavior |
|---|---|
| `/ask <prompt>` (slash command) | `interaction.response.defer()`, then `agcl.mas.run`, replies on `followup` |
| `@mention <text>` in any channel | typing indicator, then reply with the answer |
| Reply length | clipped to 2,000 chars (Discord limit) |

Defining a slash command guild-locally (instant sync vs. ~1h global)
is supported via `DISCORD_GUILD_ID`.

---

## Discord Developer Portal setup

1. Go to <https://discord.com/developers/applications> → New Application
2. Bot tab → Add Bot → copy **Bot Token**
3. OAuth2 → URL Generator → check `bot` + `applications.commands`
4. Bot Permissions: `Send Messages`, `Use Slash Commands`,
   `Read Message History`, `Embed Links`
5. Enable **Message Content Intent** (the adapter sets
   `intents.message_content = True` and Discord requires you to opt in
   from the portal too)

---

## REST-only mode (no gateway, no SDK)

For agent-to-agent posting, the SDK isn't needed. Import the helpers
directly:

```python
import asyncio
from agcl.toolkit.discord import post_message, get_me, ping

# whoami — confirms the bot token is valid
print(asyncio.run(get_me()))

# post a single message to a channel
asyncio.run(post_message(channel_id="987654321098765432",
                          content="status: build green"))

# health check — wraps get_me; never raises
print(asyncio.run(ping()))
```

`ping()` is also reachable via the toolkit CLI:

```bash
python main.py toolkit ping discord
```

---

## Env vars

```env
DISCORD_BOT_TOKEN=Bot.MTk4NjIy...           # required
DISCORD_GUILD_ID=123456789012345678         # optional: guild-scoped slash commands (instant sync)
DISCORD_AGENT_CHANNEL=987654321098765432    # optional: bus channel id, used by your own scripts
```

---

## Discord-side limits

| Limit | Value |
|---|---|
| Message character limit | 2,000 |
| Slash command response window | 3 seconds (the adapter calls `defer()` immediately) |
| Max global slash commands per bot | 100 |
| Channel message rate limit | 5 / 5s per channel |
| Bot token auth header | `Authorization: Bot <token>` |
