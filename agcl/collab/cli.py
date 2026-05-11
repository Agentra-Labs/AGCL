"""agcl.collab.cli — `agcl collab` subcommand handler."""
from __future__ import annotations

import json
import sys


def run(args) -> int:
    cmd = getattr(args, "collab_cmd", None)

    if cmd == "create":
        from agcl.collab import create_space
        space = create_space(args.name, getattr(args, "description", ""))
        print(f"space created: {space.id}")
        print(f"invite code:   {space.invite_code}")
        return 0

    if cmd == "join":
        from agcl.collab import resolve_invite, add_member, Member, get_space
        space = get_space(args.space_id)
        if not space:
            print(f"space not found: {args.space_id}", file=sys.stderr); return 1
        if space.invite_code != args.code:
            print("invalid invite code", file=sys.stderr); return 1
        member = Member(user_id="cli-user", display_name="CLI User")
        add_member(args.space_id, member)
        print(f"joined space: {args.space_id}")
        return 0

    if cmd == "send":
        from agcl.collab import post_message
        msg = post_message(args.space_id, "cli-user", "human", args.message)
        print(f"message sent: {msg.id}")
        return 0

    if cmd == "spaces":
        from agcl.collab import list_spaces
        for s in list_spaces():
            print(f"{s.id}  {s.name}  (invite: {s.invite_code})")
        return 0

    if cmd == "agents":
        from agcl.collab.agents import list_agents
        for a in list_agents():
            print(f"{a.id}  {a.name}  [{a.source}]  {a.description}")
        return 0

    if cmd == "tasks":
        from agcl.collab.tasks import list_tasks
        for t in list_tasks(args.space_id):
            print(f"{t.id}  [{t.status}]  {t.title}  assignee={t.assignee_id or '-'}")
        return 0

    print("usage: agcl collab <create|join|send|spaces|agents|tasks>")
    return 1
