"""
/setthumbnail — Re-upload a video/document with a new HD thumbnail.
Usage:
    1. Reply to a video with /setthumbnail        → auto-generate HD thumb
    2. Reply to a video and attach an image       → use attached image as thumb
    3. /setthumbnail <url>                        → download thumb from URL

The original file is re-uploaded as a video with the new thumbnail.
No re-encoding — only the thumb changes.
"""
import os
import time
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import config
from bot import LOGGER
from bot.handlers._auth import auth_required
from bot.utils.hd_thumb import generate_hd_thumb, prep_thumb
from bot.utils.size_utils import human_size


@Client.on_message(
    filters.command(["setthumbnail", "setthumb", "sthumb"]) &
    (filters.private | filters.group)
)
async def cmd_setthumbnail(client: Client, message: Message):
    if not await auth_required(message):
        return

    uid  = message.from_user.id
    rply = message.reply_to_message

    if not rply:
        return await message.reply_text(
            "╔═「 🖼 <b>SET THUMBNAIL</b> 」\n"
            "║\n"
            "║  Reply to a <b>video</b> or <b>document</b> with:\n"
            "║\n"
            "║  ➤ <code>/setthumb</code> — auto HD thumb\n"
            "║  ➤ <code>/setthumb</code> + attach image — use image\n"
            "║  ➤ <code>/setthumb &lt;url&gt;</code> — fetch thumb from URL\n"
            "╚══════════════════════",
            parse_mode=enums.ParseMode.HTML,
        )

    # ── Find the media to re-upload ───────────────────────────
    media = None
    for attr in ("video", "document", "animation", "video_note"):
        media = getattr(rply, attr, None)
        if media:
            break

    if not media:
        return await message.reply_text(
            "❌ Reply to a <b>video</b> or <b>document</b>.",
            parse_mode=enums.ParseMode.HTML,
        )

    fname     = getattr(media, "file_name", None) or f"video_{int(time.time())}.mp4"
    file_size = getattr(media, "file_size", 0)
    msg       = await message.reply_text(
        "⏳ <i>Working…</i>", parse_mode=enums.ParseMode.HTML
    )

    tmp_dir  = os.path.join(config.DOWNLOAD_DIR, f"sthumb_{uid}_{int(time.time())}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        # ── 1. Resolve the new thumbnail ──────────────────────
        thumb_path = None
        args       = message.text.split(maxsplit=1)

        # Check if user attached an image in same message
        user_photo = getattr(message, "photo", None)
        if user_photo:
            await msg.edit_text("🖼 <i>Downloading your image…</i>",
                                parse_mode=enums.ParseMode.HTML)
            raw = os.path.join(tmp_dir, "user_thumb.jpg")
            await message.download(raw)
            thumb_path = prep_thumb(raw, raw.replace(".jpg", "_hd.jpg"))

        # URL provided
        elif len(args) > 1 and args[1].startswith("http"):
            await msg.edit_text("🌐 <i>Fetching thumbnail from URL…</i>",
                                parse_mode=enums.ParseMode.HTML)
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(args[1], timeout=aiohttp.ClientTimeout(total=15)) as r:
                    raw = os.path.join(tmp_dir, "url_thumb.jpg")
                    with open(raw, "wb") as f:
                        f.write(await r.read())
            thumb_path = prep_thumb(raw, raw.replace(".jpg", "_hd.jpg"))

        # Auto-generate
        if not thumb_path:
            await msg.edit_text("🎨 <i>Generating HD thumbnail…</i>",
                                parse_mode=enums.ParseMode.HTML)
            thumb_path = await generate_hd_thumb(fname, uid=uid)

        if not thumb_path:
            return await msg.edit_text(
                "❌ Could not generate thumbnail.",
                parse_mode=enums.ParseMode.HTML,
            )

        # ── 2. Download original file ─────────────────────────
        await msg.edit_text(
            f"⬇️ <i>Downloading original ({human_size(file_size)})…</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        dest = os.path.join(tmp_dir, fname)
        await rply.download(dest)

        if not os.path.exists(dest):
            return await msg.edit_text(
                "❌ Download failed.", parse_mode=enums.ParseMode.HTML
            )

        # ── 3. Re-upload with new thumb ───────────────────────
        await msg.edit_text(
            "📤 <i>Uploading with HD thumbnail…</i>",
            parse_mode=enums.ParseMode.HTML,
        )

        caption = (
            f"╔═「 🖼 <b>HD THUMBNAIL SET</b> 」\n"
            f"║\n"
            f"║  🎬 <b>{fname}</b>\n"
            f"║  ➤ <b>Size</b> : {human_size(file_size)}\n"
            f"╚══════════════════════\n"
            f"  <i>{config.WATERMARK}</i>"
        )

        # Detect video
        is_video = dest.lower().endswith(
            (".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv")
        )

        if is_video:
            sent = await message.reply_video(
                dest,
                thumb=thumb_path,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
                supports_streaming=True,
            )
        else:
            sent = await message.reply_document(
                dest,
                thumb=thumb_path,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
            )

        await msg.delete()
        LOGGER.info(f"[SetThumb] Sent {fname} with HD thumb for uid={uid}")

    except Exception as e:
        LOGGER.error(f"[SetThumb] {e}", exc_info=True)
        await msg.edit_text(
            f"❌ <b>Failed:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
