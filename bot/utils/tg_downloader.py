"""
tg_downloader.py — Download files from Telegram message links.

Supported URL formats:
  https://t.me/channelname/123          — public channel/group post
  https://t.me/c/1234567890/123        — private channel post (needs user session)
  https://t.me/b/botusername/123       — bot message
  https://t.me/username                — just a username, no message (rejected)

How it works:
  1. Parse the URL to extract chat identifier + message ID
  2. Use the Pyrogram client to fetch the message
  3. If message has media (video/audio/document/photo) → download it
  4. Return the local file path

Requires a user session (SESSION in config) for private channel links.
Bot-only access works for public channels/groups the bot is a member of.
"""

import os
import re
import config

# ── URL patterns ──────────────────────────────────────────────
# Public:   https://t.me/username/123
# Private:  https://t.me/c/1234567890/123
_PUBLIC_RE  = re.compile(r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)", re.I)
_PRIVATE_RE = re.compile(r"https?://t\.me/c/(\d+)/(\d+)",          re.I)


def is_tg_link(url: str) -> bool:
    url = url.strip()
    return bool(_PUBLIC_RE.match(url) or _PRIVATE_RE.match(url))


def _parse(url: str) -> tuple[str | int, int] | None:
    """
    Returns (chat_id_or_username, message_id) or None if not a valid link.
    Private channel IDs are returned as negative ints (-100XXXXXXXXXX format).
    """
    m = _PRIVATE_RE.match(url.strip())
    if m:
        # Private: t.me/c/XXXXXXXXXX/MSG  → chat_id = -100XXXXXXXXXX
        chat_id = int("-100" + m.group(1))
        msg_id  = int(m.group(2))
        return chat_id, msg_id

    m = _PUBLIC_RE.match(url.strip())
    if m:
        username = m.group(1)
        msg_id   = int(m.group(2))
        # Skip single-word non-message links like t.me/username (no msg id)
        return username, msg_id

    return None


async def download_tg_link(url: str, dest_dir: str, task_id: str, msg) -> str:
    """
    Download the media from a Telegram message link.
    Returns the local file path.
    Raises ValueError for unsupported/inaccessible links.
    """
    from bot import app, user_app
    from bot.core import task_manager as tm
    from pyrogram import enums
    from bot.utils.progress import downloading_card, task_kb

    parsed = _parse(url)
    if not parsed:
        raise ValueError(f"Not a valid Telegram message link: {url}")

    chat_ref, msg_id = parsed
    is_private = isinstance(chat_ref, int) and chat_ref < 0

    # Private channel links need a user session
    client = user_app if (is_private and user_app) else app
    if is_private and not user_app:
        raise ValueError(
            "Private channel links require a Premium user session.\n"
            "Set SESSION in config.py to enable."
        )

    # Fetch the message
    try:
        tg_msg = await client.get_messages(chat_ref, msg_id)
    except Exception as e:
        raise ValueError(f"Cannot access this message: {e}")

    if not tg_msg or tg_msg.empty:
        raise ValueError("Message not found or deleted.")

    # Check for downloadable media
    media = (
        tg_msg.video    or
        tg_msg.document or
        tg_msg.audio    or
        tg_msg.photo    or
        tg_msg.animation or
        tg_msg.voice    or
        tg_msg.video_note
    )
    if not media:
        raise ValueError("This message has no downloadable media.")

    # Determine filename
    fname = (
        getattr(media, "file_name", None) or
        f"tg_{task_id}_{msg_id}" +
        _ext_from_mime(getattr(media, "mime_type", ""))
    )
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, fname)

    # Get total size for progress
    total = getattr(media, "file_size", 0)
    tm.update_progress(task_id, name=fname, done=0, total=total,
                       speed=0.0, eta=0.0, status="downloading")

    import time
    _state = {"last_t": time.monotonic(), "last_b": 0}
    kb     = task_kb(task_id)

    async def _progress(current: int, t: int):
        now = time.monotonic()
        dt  = now - _state["last_t"]
        if dt < config.PROGRESS_UPDATE_SEC:
            return
        speed = (current - _state["last_b"]) / dt if dt > 0 else 0.0
        eta   = (t - current) / speed if speed > 0 else 0.0
        _state["last_t"] = now
        _state["last_b"] = current
        tm.update_progress(task_id, name=fname, done=current,
                           total=t, speed=speed, eta=eta, status="downloading")
        try:
            await msg.edit_text(
                downloading_card(fname, current, t, speed, eta, task_id),
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    # Download
    path = await client.download_media(
        tg_msg,
        file_name=dest,
        progress=_progress,
    )
    if not path or not os.path.isfile(path):
        raise ValueError("Download failed — no file saved.")

    return path


def _ext_from_mime(mime: str) -> str:
    _map = {
        "video/mp4":        ".mp4",
        "video/x-matroska": ".mkv",
        "video/webm":       ".webm",
        "video/x-msvideo":  ".avi",
        "audio/mpeg":       ".mp3",
        "audio/ogg":        ".ogg",
        "audio/flac":       ".flac",
        "image/jpeg":       ".jpg",
        "image/png":        ".png",
        "application/zip":  ".zip",
        "application/pdf":  ".pdf",
    }
    return _map.get(mime.lower().split(";")[0].strip(), ".bin")
