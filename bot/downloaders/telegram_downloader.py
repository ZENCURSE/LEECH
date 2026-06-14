"""
Telegram Downloader — NXTL
Downloads files directly from Telegram messages using the bot or user session.
Supports HyperDL (parallel download via helper bots) when available.
Progress is reported via the task_manager.
"""
import asyncio
import time
from secrets import token_hex

from bot.core import task_manager as tm


async def telegram_download(
    message,
    dest_path: str,
    task_id: str,
    msg,
    client=None,
    user_client=None,
) -> str:
    """
    Download the media from a Telegram message.
    - message: pyrogram Message object containing media
    - dest_path: destination file path (including filename)
    - task_id: task identifier for progress tracking
    - msg: status message to update
    - client: bot client (default)
    - user_client: user session client for 4 GB files (optional)

    Returns the local path of the downloaded file.
    """
    if not message.media:
        raise ValueError("No media found in the message")

    media = getattr(message, message.media.value, None)
    if media is None:
        raise ValueError("Unable to extract media from message")

    total = getattr(media, "file_size", 0) or 0
    name  = getattr(media, "file_name", None) or dest_path.rsplit("/", 1)[-1] or "download"

    processed = [0]
    last_edit  = [0.0]

    async def _progress(current, _total):
        if tm.is_cancelled(task_id):
            # Stop transmission on cancel
            if user_client:
                user_client.stop_transmission()
            elif client:
                client.stop_transmission()
            raise asyncio.CancelledError

        processed[0] = current
        speed = current / max(time.monotonic() - start_time, 0.001)
        eta   = (total - current) / speed if speed and total > current else 0

        tm.update_progress(
            task_id,
            name=name,
            done=current,
            total=total or current,
            speed=speed,
            eta=eta,
            status="downloading",
        )

    start_time = time.monotonic()
    tm.set_status(task_id, "downloading")

    # Choose client: user session > bot
    dl_client = user_client if user_client else client

    try:
        downloaded = await message.download(
            file_name=dest_path,
            progress=_progress,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        raise RuntimeError(f"Telegram download failed: {e}") from e

    if downloaded is None:
        raise RuntimeError("Telegram download returned no file")

    return downloaded
