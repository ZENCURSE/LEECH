"""
file_links.py — short-lived tokens for the "📥 View File in PM" button.

A completed leech in a group gets a deep-link button:
    https://t.me/<bot>?start=gf_<token>
Tapping it opens PM with the bot, which reads the token, looks up which
group message the file lives in, and copies it straight into the user's
PM. Tokens are in-memory only (not worth persisting across restarts —
by the time the bot restarts, old completion messages are stale anyway)
and expire after 24h so the dict can't grow unbounded.
"""

import secrets
import time

_TTL = 24 * 3600
_links: dict[str, tuple[int, int, float]] = {}


def _prune() -> None:
    now = time.time()
    dead = [k for k, (_, _, ts) in _links.items() if now - ts > _TTL]
    for k in dead:
        _links.pop(k, None)


def make_token(chat_id: int, message_id: int) -> str:
    if len(_links) > 2000:
        _prune()
    token = secrets.token_urlsafe(6)
    _links[token] = (chat_id, message_id, time.time())
    return token


def resolve(token: str) -> tuple[int, int] | None:
    entry = _links.get(token)
    if not entry:
        return None
    chat_id, message_id, ts = entry
    if time.time() - ts > _TTL:
        _links.pop(token, None)
        return None
    return chat_id, message_id


_bot_username: str | None = None


async def get_bot_username(client) -> str:
    global _bot_username
    if _bot_username is None:
        me = await client.get_me()
        _bot_username = me.username
    return _bot_username
