"""
Slack AI Apps integration via Bolt for Python.

Wires up the three required event subscriptions
(assistant_thread_started, assistant_thread_context_changed, message.im)
and pipes user messages into AGCL's recursive MAS engine. Replies are
streamed back as Slack messages.

Required env vars:
    SLACK_BOT_TOKEN       xoxb-... (from your Slack app)
    SLACK_SIGNING_SECRET  signing secret (HTTP mode)
  OR
    SLACK_APP_TOKEN       xapp-... (Socket Mode mode; preferred for dev)

Required OAuth scopes:
    assistant:write, chat:write, channels:history, im:history, im:read

Required event subscriptions:
    assistant_thread_started
    assistant_thread_context_changed
    message.im

Usage:
    pip install slack-bolt
    python main.py slack                  # auto-picks Socket Mode if app token set
    python main.py slack --http --port 3000   # HTTP mode
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

from .manifest import call_tool


SDK_HINT = (
    "Slack Bolt SDK not installed. Install with:\n"
    "    pip install slack-bolt\n"
    "and re-run."
)


def _build_app():
    try:
        from slack_bolt import App                          # type: ignore
    except Exception:
        return None, None
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    signing   = os.environ.get("SLACK_SIGNING_SECRET")
    if not bot_token:
        print("ERROR: SLACK_BOT_TOKEN env var is required.", file=sys.stderr)
        return None, None
    app = App(token=bot_token, signing_secret=signing)

    @app.event("assistant_thread_started")
    def thread_started(event, client):
        thread_ts = event.get("assistant_thread", {}).get("thread_ts")
        channel   = event.get("assistant_thread", {}).get("channel_id")
        if not thread_ts or not channel:
            return
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="AGCL ready. Send a message to run a recursive MAS turn.",
        )

    @app.event("assistant_thread_context_changed")
    def context_changed(event):
        # Slack pushes new context (channel set, file added, etc.). We
        # don't currently route on this but log the shape so users can
        # extend if they want.
        return None

    @app.event("message")
    def on_message(event, say):
        # Skip our own messages and edits.
        if event.get("subtype") in ("message_changed", "message_deleted"):
            return
        if event.get("bot_id"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        sid = f"slack-{event.get('user') or 'anon'}"
        try:
            result = call_tool("agcl.mas.run", {
                "message": text, "session_id": sid,
            })
            say(text=result.get("answer", "(no answer)"),
                thread_ts=event.get("thread_ts") or event.get("ts"))
        except Exception as e:
            say(text=f"AGCL error: {type(e).__name__}: {e}",
                thread_ts=event.get("thread_ts") or event.get("ts"))

    return app, "ready"


def run(http: bool = False, port: int = 3000) -> int:
    app, _ = _build_app()
    if app is None:
        print(SDK_HINT, file=sys.stderr)
        return 1

    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not http and app_token:
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler  # type: ignore
        except Exception:
            print("ERROR: install with: pip install slack-bolt[socket-mode]",
                  file=sys.stderr)
            return 1
        print("AGCL Slack adapter starting (Socket Mode)")
        SocketModeHandler(app, app_token).start()
        return 0

    print(f"AGCL Slack adapter starting (HTTP) on :{port}/slack/events")
    app.start(port=port)
    return 0
