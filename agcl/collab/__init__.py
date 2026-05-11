"""agcl.collab — collaboration platform data layer and API."""
from agcl.collab.spaces import (
    Space, Member, Message,
    create_space, get_space, save_space, list_spaces, delete_space,
    resolve_invite, add_member, get_member, get_members,
    post_message, get_messages, update_message, message_count,
)

__all__ = [
    "Space", "Member", "Message",
    "create_space", "get_space", "save_space", "list_spaces", "delete_space",
    "resolve_invite", "add_member", "get_member", "get_members",
    "post_message", "get_messages", "update_message", "message_count",
]
