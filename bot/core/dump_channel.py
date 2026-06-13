"""
Dump Channel — NXTL
Forwards every leeched file to the owner's dump channel after upload.
Configure DUMP_CHANNEL in config.py with a channel/group ID (negative).

The bot must be an admin in the dump channel with "Post Messages" permission.

Usage:
    from bot.core.dump_channel import send_to_dump
    await send_to_dump(client, sent_msg, user, filename)
"""

import config
from pyrogram import enums

_DUMP_CHANNEL: int = getattr(config, "DUMP_CHANNEL", 0)
_DUMP_TAG: bool    = getattr(config, "DUMP_CHANNEL_TAG", True)


def is_dump_enabled() -> bool:
    """Return True if a dump channel is configured."""
    return bool(_DUMP_CHANNEL)


async def send_to_dump(client, sent_message, user=None, filename: str = "") -> None:
    """
    Forward a leeched file to the owner's dump channel.

    Args:
        client:       Pyrogram client (bot)
        sent_message: The Pyrogram Message that was successfully uploaded
        user:         Pyrogram User object of the requester (optional)
        filename:     Human-readable filename for the caption
    """
    if not _DUMP_CHANNEL:
        return

    try:
        # Build caption
        caption_parts = []
        if filename:
            caption_parts.append(f"📄 <code>{filename}</code>")
        if user and _DUMP_TAG:
            name  = f"{user.first_name or ''} {user.last_name or ''}".strip()
            uname = f"@{user.username}" if user.username else f"<code>{user.id}</code>"
            caption_parts.append(f"👤 {name} ({uname})")

        caption = "\n".join(caption_parts) or None

        # Forward the message to the dump channel
        await client.copy_message(
            chat_id=_DUMP_CHANNEL,
            from_chat_id=sent_message.chat.id,
            message_id=sent_message.id,
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        # Silently log — never break the user's upload because of dump
        try:
            from bot import app as _app
            import logging
            logging.getLogger(__name__).warning(
                f"Dump channel send failed (chat={_DUMP_CHANNEL}): {e}"
            )
        except Exception:
            pass


async def send_raw_to_dump(client, chat_id: int, message_id: int, caption: str = "") -> None:
    """
    Forward any message by chat_id + message_id to the dump channel.
    Useful for bulk/multi-file leech where individual messages are sent separately.
    """
    if not _DUMP_CHANNEL:
        return
    try:
        await client.copy_message(
            chat_id=_DUMP_CHANNEL,
            from_chat_id=chat_id,
            message_id=message_id,
            caption=caption or None,
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"Dump channel raw send failed: {e}"
        )
