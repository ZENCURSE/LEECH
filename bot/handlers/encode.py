"""
Encode handler — integrated from ENCODING-BOT into NXT_HUB v5

Commands:
  /encode     — reply to a video file (TG) to encode it
  /encurl     — /encurl <url> [|filename] — download URL and encode
  /encset     — open encoding settings
  /vset       — view current encoding settings
"""
import os
import asyncio
import traceback

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import config
from bot.handlers._auth import auth_required
from bot.database import users_db
from bot.encoding.encoding import encode, get_duration, get_width_height
from bot.encoding.helper import handle_encode


# ── /encode — reply to TG file ────────────────────────────────

@Client.on_message(filters.command("encode") & (filters.private | filters.group))
async def cmd_encode(client: Client, message: Message):
    if not await auth_required(message):
        return

    replied = message.reply_to_message
    if not (replied and (replied.video or replied.document)):
        await message.reply_text(
            "❌ Reply to a video/document with <code>/encode</code> to encode it.\n\n"
            "Use <code>/encset</code> to configure encoding settings first.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    msg = await message.reply_text(
        "📥 <b>Downloading for encoding…</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        # Download
        media = replied.video or replied.document
        fname = getattr(media, "file_name", None) or f"video_{message.id}.mkv"
        dest  = os.path.join(config.DOWNLOAD_DIR, f"enc_{message.id}", fname)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        await client.download_media(replied, file_name=dest)

        if not os.path.isfile(dest):
            await msg.edit_text("❌ Download failed.", parse_mode=enums.ParseMode.HTML)
            return

        await msg.edit_text("⚙️ <b>Encoding…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(dest, message, msg)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Encode error:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ── /encurl — download URL and encode ─────────────────────────

@Client.on_message(filters.command("encurl") & (filters.private | filters.group))
async def cmd_encurl(client: Client, message: Message):
    if not await auth_required(message):
        return

    parts = message.text.split(None, 2)
    if len(parts) < 2:
        await message.reply_text(
            "❌ Usage: <code>/encurl &lt;url&gt; [| filename]</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    url      = parts[1].strip()
    filename = parts[2].strip() if len(parts) > 2 else None

    msg = await message.reply_text(
        "📥 <b>Downloading…</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        from bot.core.downloader import http_download, ytdlp_download
        from bot.utils.direct_links import resolve

        dest_dir = os.path.join(config.DOWNLOAD_DIR, f"enc_url_{message.id}")
        os.makedirs(dest_dir, exist_ok=True)

        info = await resolve(url)
        if info["use_ytdlp"]:
            filepath = await ytdlp_download(info["url"], dest_dir, f"encurl_{message.id}", msg, message.from_user.id)
        else:
            filepath = await http_download(info["url"], dest_dir, f"encurl_{message.id}", msg)

        # Rename if custom filename given
        if filename:
            ext      = os.path.splitext(filepath)[1]
            new_path = os.path.join(dest_dir, filename + ext)
            os.rename(filepath, new_path)
            filepath = new_path

        await msg.edit_text("⚙️ <b>Encoding…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(filepath, message, msg)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Error:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ── /encset — open encoding settings ─────────────────────────

@Client.on_message(filters.command("encset") & (filters.private | filters.group))
async def cmd_encset(client: Client, message: Message):
    if not await auth_required(message):
        return
    try:
        from bot.encoding.settings_utils import OpenSettings
        from bot.encoding.db import enc_db
        await enc_db.add_user(message.from_user.id)
        editable = await message.reply_text("⚙️ Loading settings…")
        await OpenSettings(editable, user_id=message.from_user.id)
    except Exception as e:
        await message.reply_text(
            f"❌ Settings error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )


# ── /vset — view encoding settings ─────────────────────────────

@Client.on_message(filters.command("vset") & (filters.private | filters.group))
async def cmd_vset(client: Client, message: Message):
    if not await auth_required(message):
        return
    try:
        from bot.encoding.db import enc_db
        uid = message.from_user.id

        crf    = await enc_db.get_crf(uid)
        codec  = "H.265" if await enc_db.get_hevc(uid) else "H.264"
        preset = await enc_db.get_preset(uid) or "slow"
        res    = await enc_db.get_resolution(uid) or "OG"
        audio  = await enc_db.get_audio(uid) or "copy"
        ext    = await enc_db.get_extensions(uid) or "MKV"
        hardsub = "✅" if await enc_db.get_hardsub(uid) else "❌"
        softsub = "✅" if await enc_db.get_subtitles(uid) else "❌"
        watermark = "✅" if await enc_db.get_watermark(uid) else "❌"

        text = (
            "<b>⚙️ Your Encoding Settings</b>\n\n"
            f"🎬 <b>Codec:</b> <code>{codec}</code>\n"
            f"📹 <b>CRF:</b> <code>{crf}</code>\n"
            f"🚀 <b>Preset:</b> <code>{preset}</code>\n"
            f"📐 <b>Resolution:</b> <code>{'Source' if res == 'OG' else res + 'p'}</code>\n"
            f"🔊 <b>Audio:</b> <code>{audio.upper()}</code>\n"
            f"📄 <b>Output:</b> <code>{ext}</code>\n"
            f"📜 <b>Hardsub:</b> {hardsub}  <b>Softsub:</b> {softsub}\n"
            f"💧 <b>Watermark:</b> {watermark}\n\n"
            "Use /encset to change settings."
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(
            f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
