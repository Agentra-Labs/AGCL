# Deploy AGCL as a Discord bot

Wrap the AGCL agent in a Discord bot that responds to `/ask` slash
commands and `@mention`s. Every reply goes through the same MAS
reasoning loop your web console hits.

> Source: [agcl/toolkit/discord.py](../../agcl/toolkit/discord.py)
> · reference: [docs/integrations/discord.md](../integrations/discord.md)

---

## What you're building

```
        Discord                          your machine / VPS
        ───────                          ────────────────────
        /ask "..."   ──── Gateway ───►   discord.py bot
        @mention                         │
                                          ▼
                                    call_tool("agcl.mas.run", ...)
                                          │
                                          ▼
                                    AGCL MAS / cloud reply
                                          │
                                          ▼
   Reply back  ◄──── REST (HTTPS) ────  bot.followup.send(...)
```

The bot can run in the same process as your AGCL node, or in its own
process talking to a node over HTTP. The simplest setup is "same
process": one long-running command holds both.

## Prerequisites

| Need                                        | How to get it                                                            |
|---------------------------------------------|---------------------------------------------------------------------------|
| A Discord account                           | discord.com                                                              |
| A working AGCL clone (local or hosted)      | [web_socket/README.md](../../web_socket/README.md)                       |
| `discord.py` Python lib                     | `pip install discord.py`                                                  |
| (Optional) A test Guild ID                  | Right-click your server in Discord → Copy Server ID (Developer Mode on)  |

---

## Step 1 — Create the bot application

1. Go to https://discord.com/developers/applications and click
   **New Application**.
2. Name it ("AGCL"), accept the terms.
3. Side panel → **Bot** → **Reset Token** → copy the token. **This is
   your `DISCORD_BOT_TOKEN`.** It will never be shown again — store it.
4. Same page, scroll down to **Privileged Gateway Intents**:
   - Turn ON **Message Content Intent** (otherwise `@mention` won't work).
   - **Server Members Intent** and **Presence Intent** can stay off.
5. Side panel → **OAuth2 → URL Generator**:
   - **Scopes**: `bot` + `applications.commands`
   - **Bot Permissions**: `Send Messages`, `Read Message History`,
     `Use Slash Commands`, `Embed Links`
6. Copy the generated URL, open it in a new tab, **select your test
   server**, click **Authorize**.

The bot is now a member of that server (offline until you start it).

---

## Step 2 — Configure AGCL

Add the bot token to `.env`:

```bash
echo "DISCORD_BOT_TOKEN=your-token-here" >> .env
# Optional: register slash commands instantly in just one guild
# (without this, slash commands take up to 1 hour to propagate globally)
echo "DISCORD_GUILD_ID=123456789012345678" >> .env
```

Or use the web console: Setup → Cloud providers → **Reload .env**
after editing, then the new env var is live without restarting.

Make sure `discord.py` is installed:

```bash
pip install discord.py
# (or: uv pip install discord.py)
```

---

## Step 3 — Smoke-test the token

Before starting the gateway, confirm the token is valid:

```bash
python -c "
import asyncio, os
from agcl.toolkit.discord import get_me
print(asyncio.run(get_me()))
"
# → {'id': '...', 'username': 'AGCL', 'discriminator': '0', ...}
```

If you get a 401, the token is wrong (most common) or has been
reset by another login.

You can also do this from the GUI: **Toolkit** tab → find
`discord` in the discovery table → click **Ping**.

---

## Step 4 — Start the bot

```bash
python main.py slack --help        # (slack is a sibling adapter — same pattern)

# Actually start the discord bot:
python main.py toolkit discord     # or: python -m agcl.toolkit.discord
```

What you should see:

```
AGCL Discord adapter starting (gateway)
AGCL Discord adapter ready as AGCL#1234
```

If you set `DISCORD_GUILD_ID`, slash commands register in that one
server within a few seconds. Without it, registration is global and
can take up to an hour the first time.

> The bot needs an AGCL node available *in the same Python process*
> (it uses `call_tool` directly, not HTTP). If you want a separate
> deployment topology — bot in one container, node in another — see
> "Bot-in-its-own-process" below.

---

## Step 5 — Test it in Discord

In a channel where the bot has access:

```
/ask  prompt: What's a concise definition of "agentic AI"?
```

You should see "AGCL is thinking…" replaced by the answer a few
seconds later.

Or `@mention` the bot:

```
@AGCL Summarize this thread in three bullet points.
```

Both paths call `agcl.mas.run` with `session_id = discord-<user_id>`,
so each Discord user gets their own session.

---

## Bot-in-its-own-process (recommended for production)

If you want to scale the bot independently of the node, run it in a
separate container that calls the node over HTTP. Two changes:

1. Replace `call_tool(...)` with a direct HTTP call to
   `POST /node/mas/run` on the node's URL.
2. Set the bearer key in the bot's env (`AGCL_NODE_AUTH`) so it can
   authenticate.

A minimal wrapper (`bot_http.py`):

```python
import os
import asyncio
import httpx
import discord
from discord import app_commands
from discord.ext import commands

NODE_URL = os.environ["AGCL_NODE_URL"]            # e.g. https://agcl-node-…run.app
NODE_KEY = os.environ["AGCL_NODE_AUTH"]
TOKEN    = os.environ["DISCORD_BOT_TOKEN"]

intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()

@bot.tree.command(name="ask")
async def ask(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer()
    async with httpx.AsyncClient(timeout=300) as h:
        r = await h.post(
            f"{NODE_URL}/node/mas/run",
            headers={"Authorization": f"Bearer {NODE_KEY}"},
            json={"message": prompt, "session_id": f"discord-{interaction.user.id}"},
        )
        j = r.json()
    await interaction.followup.send(j.get("answer", "(no answer)")[:2000])

bot.run(TOKEN)
```

Now you can run the bot in a tiny container with no model weights,
hitting an AGCL node deployed elsewhere ([gcp.md](gcp.md),
[k8s.md](k8s.md), [docker.md](docker.md)).

---

## Verify it works

| Check                              | Expected                                                        |
|------------------------------------|------------------------------------------------------------------|
| `python -c "from agcl.toolkit.discord import get_me; import asyncio; print(asyncio.run(get_me()))"` | bot user JSON with the username you chose |
| Bot online in Discord member list  | green dot                                                        |
| `/ask` command visible             | type `/` in any channel — autocompletion shows `ask`             |
| Reply in <30s                      | answer text (truncated at 2000 chars per Discord limit)          |

The third check often fails the first time because slash commands
take up to an hour to register globally. Set `DISCORD_GUILD_ID`
during development to make registration instant.

---

## Common problems

| Symptom                                                | Fix                                                                                                                            |
|--------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `401 Unauthorized` from `get_me()`                     | Token wrong / regenerated. Reset in the Developer Portal and update `.env`.                                                     |
| Slash command `/ask` doesn't appear                    | Set `DISCORD_GUILD_ID` to a test server's ID for instant registration; otherwise wait up to an hour.                            |
| Bot replies to `/ask` but not to `@mention`            | "Message Content Intent" off in the Developer Portal. Turn it on, restart.                                                      |
| `discord.py SDK not installed`                         | `pip install discord.py` (yes, it's `discord.py`, not `discord`).                                                                |
| Bot crashes on startup with `RuntimeError: This event loop is already running` | You started it from a Jupyter cell. Run it from a normal shell.                                                              |
| Replies cut off at 2000 chars                          | Discord per-message cap. Split long answers, or use embeds.                                                                      |
| Bot can see DMs but not channels                       | Hasn't been invited or has no permission. Re-run the OAuth URL with the bot scopes.                                              |

---

## Operating notes

- **Sessions**: every Discord user gets `session_id = discord-<user_id>`.
  Sessions persist to `STATE_DIR` like normal AGCL sessions, so a user
  can resume context across days.
- **Concurrency**: discord.py is single-process; the AGCL backend
  serializes work per-session but parallelizes across sessions.
  ~50 simultaneous users on a CPU node is realistic.
- **Privacy**: the bot's reasoning step is sent to whichever cloud
  provider is configured. If your guild has rules against this,
  use a self-hosted model ([vllm.md](vllm.md), [ollama.md](ollama.md))
  + set `DEFAULT_CLOUD` accordingly.
- **Rate limits**: Discord caps at ~50 messages/sec across the bot.
  AGCL's per-session quotas ([usage tab](../web-console.md#tabs-and-which-endpoints-each-one-drives))
  protect you from runaway spend.

---

## What to read next

- Slack equivalent: [docs/integrations/slack.md](../integrations/slack.md)
- Multiple parallel users → multi-replica node setup: [k8s.md](k8s.md)
- Lock down the bot's reasoning to a self-hosted model: [vllm.md](vllm.md)
