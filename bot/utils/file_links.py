"""
file_links.py — small cache for the bot's own username.

Used by the "📥 View File in PM" button on group completion messages,
which just opens a chat with the bot (https://t.me/<username>).
"""

_bot_username: str | None = None


async def get_bot_username(client) -> str:
    global _bot_username
    if _bot_username is None:
        me = await client.get_me()
        _bot_username = me.username
    return _bot_username
