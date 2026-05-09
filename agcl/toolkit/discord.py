"""
Discord adapter — full implementation routing through the AGCL tool
manifest. Mirrors agcl.integrations.slack_bolt's pattern:

    - Slash command /ask  -> call_tool("agcl.mas.run", {...})
    - @mention in any channel -> same
    - REST-only fallback for ultra-light agent-to-agent posting

discord.py is an optional dep — `pip install discord.py`. Without it,
the REST helpers still work (raw httpx).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, Optional

import httpx

from agcl.integrations.manifest import call_tool


SDK_HINT = (
    "discord.py SDK not installed. Install with:\n"
    "    pip install discord.py\n"
    "and re-run.  (REST helpers still work without the SDK.)"
)


# ----------------------------------------------------------------------
# REST-only helpers (no gateway, no SDK)
# ----------------------------------------------------------------------

API = "https://discord.com/api/v10"


async def post_message(channel_id: str, content: str,
                        bot_token: Optional[str] = None) -> Dict[str, Any]:
    """Post a single message to a channel. Use as an agent-to-agent bus."""
    token = bot_token or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN required")
    async with httpx.AsyncClient(timeout=15) as h:
        r = await h.post(
            f"{API}/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {token}",
                     "Content-Type": "application/json"},
            json={"content": content[:2000]},
        )
        r.raise_for_status()
        return r.json()


async def get_me(bot_token: Optional[str] = None) -> Dict[str, Any]:
    """Whoami — confirms the bot token is valid."""
    token = bot_token or os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN required")
    async with httpx.AsyncClient(timeout=10) as h:
        r = await h.get(
            f"{API}/users/@me",
            headers={"Authorization": f"Bot {token}"},
        )
        r.raise_for_status()
        return r.json()


async def ping() -> Dict[str, Any]:
    if not os.environ.get("DISCORD_BOT_TOKEN"):
        return {"ok": False, "reason": "DISCORD_BOT_TOKEN unset"}
    try:
        me = await get_me()
        return {"ok": True, "id": me.get("id"), "username": me.get("username")}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "status": e.response.status_code,
                "error": e.response.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Full bot (gateway + slash commands)
# ----------------------------------------------------------------------

def _build_bot():
    try:
        import discord                            # type: ignore
        from discord import app_commands          # type: ignore
        from discord.ext import commands          # type: ignore
    except ImportError:
        return None

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN env var is required.", file=sys.stderr)
        return None

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    tree = bot.tree

    @bot.event
    async def on_ready():  # pragma: no cover
        guild_id = os.environ.get("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        else:
            await tree.sync()
        print(f"AGCL Discord adapter ready as {bot.user}")

    @tree.command(name="ask", description="Ask the AGCL agent")
    @app_commands.describe(prompt="Your question or task")
    async def ask(interaction: "discord.Interaction", prompt: str):
        await interaction.response.defer()
        sid = f"discord-{interaction.user.id}"
        try:
            result = await asyncio.to_thread(
                call_tool, "agcl.mas.run",
                {"message": prompt, "session_id": sid},
            )
            answer = result.get("answer", "(no answer)")
        except Exception as e:
            answer = f"AGCL error: {type(e).__name__}: {e}"
        await interaction.followup.send(answer[:2000])

    @bot.event
    async def on_message(message):  # pragma: no cover
        if message.author.bot:
            return
        if bot.user is None or not bot.user.mentioned_in(message):
            return
        text = message.clean_content
        sid = f"discord-{message.author.id}"
        async with message.channel.typing():
            try:
                result = await asyncio.to_thread(
                    call_tool, "agcl.mas.run",
                    {"message": text, "session_id": sid},
                )
                answer = result.get("answer", "(no answer)")
            except Exception as e:
                answer = f"AGCL error: {type(e).__name__}: {e}"
        await message.reply(answer[:2000])

    return bot


def run() -> int:
    bot = _build_bot()
    if bot is None:
        print(SDK_HINT, file=sys.stderr)
        return 1
    token = os.environ["DISCORD_BOT_TOKEN"]
    print("AGCL Discord adapter starting (gateway)")
    bot.run(token)
    return 0
