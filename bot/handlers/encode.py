"""
Encode handler — NXT_HUB v5

Commands:
  /encode        — reply to a video file to encode it
  /encurl <url>  — download URL and encode
  /encsub        — upload video + subtitle together for hardsub
  /encset        — open encoding settings panel
  /vset          — view current encode settings

Sub+Video upload flows:
  Flow A: /encsub → bot asks for video → user sends video → bot asks for sub → user sends sub → encode
  Flow B: /encode replying to video, with a .srt/.ass doc attached in same message
  Flow C: /encsub <video_url> <sub_url> — both as URLs
"""
import os
import asyncio
import shutil

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import config
from bot.handlers._auth import auth_required
from bot.database import users_db
from bot.encoding.helper import handle_encode

# ── Waiting state for multi-step /encsub flow ─────────────────
# {uid: {"step": "video"|"sub", "video_path": str, "work_dir": str}}
_encsub_state: dict[int, dict] = {}
_encsub_timeout: dict[int, asyncio.Task] = {}

SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}


def _cancel_encsub(uid: int):
    t = _encsub_timeout.pop(uid, None)
    if t and not t.done():
        t.cancel()
    _encsub_state.pop(uid, None)


async def _encsub_expire(uid: int, msg, secs: int = 120):
    await asyncio.sleep(secs)
    if uid in _encsub_state:
        _encsub_state.pop(uid, None)
        try:
            await msg.edit_text(
                "⏰ <b>/encsub timed out.</b> Send /encsub to start again.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


# ── Convert subtitle to .ass (for consistent hardsub rendering) ─

async def _to_ass(src: str, dest_dir: str) -> str:
    """Convert any subtitle format to .ass using ffmpeg. Returns .ass path."""
    name = os.path.splitext(os.path.basename(src))[0]
    out  = os.path.join(dest_dir, name + ".ass")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", src, out,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return out if os.path.isfile(out) else src


# ══════════════════════════════════════════════════════════════
#  /encsub — video + external subtitle → hardsub
# ══════════════════════════════════════════════════════════════

@Client.on_message(filters.command("encsub") & (filters.private | filters.group))
async def cmd_encsub(client: Client, message: Message):
    if not await auth_required(message):
        return

    uid   = message.from_user.id
    parts = message.text.split(None, 3)

    # ── Flow C: /encsub <video_url> <sub_url> ─────────────────
    if len(parts) >= 3:
        video_url = parts[1].strip()
        sub_url   = parts[2].strip()
        msg = await message.reply_text(
            "📥 <b>Downloading video…</b>", parse_mode=enums.ParseMode.HTML
        )
        try:
            from bot.core.downloader import http_download, ytdlp_download
            from bot.utils.direct_links import resolve

            work_dir = os.path.join(config.DOWNLOAD_DIR, f"encsub_{message.id}")
            os.makedirs(work_dir, exist_ok=True)

            # Download video
            info = await resolve(video_url)
            if info["use_ytdlp"]:
                video_path = await ytdlp_download(info["url"], work_dir, f"encsub_v_{message.id}", msg, uid)
            else:
                video_path = await http_download(info["url"], work_dir, f"encsub_v_{message.id}", msg)

            await msg.edit_text(
                "📥 <b>Video done. Downloading subtitle…</b>",
                parse_mode=enums.ParseMode.HTML,
            )

            # Download subtitle
            sub_path = await http_download(sub_url, work_dir, f"encsub_s_{message.id}", msg)
            sub_path = await _to_ass(sub_path, work_dir)

            await msg.edit_text("⚙️ <b>Encoding with hardsub…</b>", parse_mode=enums.ParseMode.HTML)
            await handle_encode(video_path, message, msg, external_sub=sub_path)

        except Exception as e:
            await msg.edit_text(
                f"❌ <b>Error:</b>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        return

    # ── Flow A: interactive two-step ──────────────────────────
    _cancel_encsub(uid)
    work_dir = os.path.join(config.DOWNLOAD_DIR, f"encsub_{uid}_{message.id}")
    os.makedirs(work_dir, exist_ok=True)
    _encsub_state[uid] = {"step": "video", "work_dir": work_dir}

    prompt = await message.reply_text(
        "🎬 <b>Step 1/2 — Send your video</b>\n\n"
        "Send the video file (or reply to one) that you want to encode.\n"
        "Supported: <code>.mkv .mp4 .avi .mov .ts</code>\n\n"
        "<i>Waiting 2 minutes…</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    _encsub_timeout[uid] = asyncio.ensure_future(_encsub_expire(uid, prompt, 120))


# ── Receive video in /encsub flow ─────────────────────────────

@Client.on_message(
    (filters.video | filters.document) & (filters.private | filters.group),
    group=3,
)
async def encsub_receive_video(client: Client, message: Message):
    if not message.from_user:
        return
    uid   = message.from_user.id
    state = _encsub_state.get(uid)
    if not state or state["step"] != "video":
        return

    media = message.video or message.document
    if not media:
        return

    # Accept video files or documents with video extension
    fname = getattr(media, "file_name", None) or "video.mkv"
    ext   = os.path.splitext(fname)[1].lower()
    video_exts = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m4v", ".flv", ".webm", ".wmv"}

    # If it's a document, check it's a video extension
    if message.document and ext not in video_exts:
        # Could be the subtitle sent out of order — ignore here
        return

    _cancel_encsub(uid)
    work_dir = state["work_dir"]

    msg = await message.reply_text(
        "📥 <b>Downloading video…</b>", parse_mode=enums.ParseMode.HTML
    )

    dest = os.path.join(work_dir, fname)
    await client.download_media(media.file_id, file_name=dest)

    if not os.path.isfile(dest):
        await msg.edit_text("❌ Video download failed.", parse_mode=enums.ParseMode.HTML)
        _encsub_state.pop(uid, None)
        return

    _encsub_state[uid] = {"step": "sub", "video_path": dest, "work_dir": work_dir, "msg": msg}

    await msg.edit_text(
        "✅ <b>Video received!</b>\n\n"
        "📄 <b>Step 2/2 — Send your subtitle file</b>\n\n"
        "Supported: <code>.srt .ass .ssa .vtt .sub</code>\n\n"
        "<i>Waiting 2 minutes…</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    _encsub_timeout[uid] = asyncio.ensure_future(
        _encsub_expire(uid, msg, 120)
    )


# ── Receive subtitle in /encsub flow ──────────────────────────

@Client.on_message(filters.document & (filters.private | filters.group), group=4)
async def encsub_receive_sub(client: Client, message: Message):
    if not message.from_user:
        return
    uid   = message.from_user.id
    state = _encsub_state.get(uid)
    if not state or state["step"] != "sub":
        return

    doc  = message.document
    fname = doc.file_name or "subtitle.srt"
    ext   = os.path.splitext(fname)[1].lower()

    if ext not in SUB_EXTS:
        await message.reply_text(
            f"❌ Unsupported subtitle format <code>{ext}</code>\n"
            f"Accepted: {' '.join(SUB_EXTS)}",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    _cancel_encsub(uid)
    video_path = state["video_path"]
    work_dir   = state["work_dir"]
    msg        = state.get("msg")
    _encsub_state.pop(uid, None)

    if not msg:
        msg = await message.reply_text("⚙️ Processing…", parse_mode=enums.ParseMode.HTML)

    try:
        await msg.edit_text(
            "📥 <b>Downloading subtitle…</b>", parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass

    sub_dest = os.path.join(work_dir, fname)
    await client.download_media(doc.file_id, file_name=sub_dest)

    if not os.path.isfile(sub_dest):
        await msg.edit_text("❌ Subtitle download failed.", parse_mode=enums.ParseMode.HTML)
        return

    # Convert to .ass for best hardsub compatibility
    sub_ass = await _to_ass(sub_dest, work_dir)

    try:
        await msg.edit_text(
            "⚙️ <b>Encoding with hardsub…</b>\n\n"
            f"🎬 <code>{os.path.basename(video_path)}</code>\n"
            f"📄 <code>{os.path.basename(sub_ass)}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass

    await handle_encode(video_path, message, msg, external_sub=sub_ass)


# ══════════════════════════════════════════════════════════════
#  /encode — reply to video, optionally with subtitle attached
# ══════════════════════════════════════════════════════════════

@Client.on_message(filters.command("encode") & (filters.private | filters.group))
async def cmd_encode(client: Client, message: Message):
    if not await auth_required(message):
        return

    replied = message.reply_to_message
    if not (replied and (replied.video or replied.document)):
        await message.reply_text(
            "❌ Reply to a video/document with <code>/encode</code>.\n\n"
            "To hardsub with external subtitle use <code>/encsub</code>.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    msg = await message.reply_text(
        "📥 <b>Downloading for encoding…</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        media = replied.video or replied.document
        fname = getattr(media, "file_name", None) or f"video_{message.id}.mkv"
        work_dir = os.path.join(config.DOWNLOAD_DIR, f"enc_{message.id}")
        os.makedirs(work_dir, exist_ok=True)
        dest = os.path.join(work_dir, fname)

        await client.download_media(replied, file_name=dest)

        if not os.path.isfile(dest):
            await msg.edit_text("❌ Download failed.", parse_mode=enums.ParseMode.HTML)
            return

        # Check if a subtitle doc is attached to the /encode message itself
        external_sub = None
        if message.document:
            sub_fname = message.document.file_name or ""
            if os.path.splitext(sub_fname)[1].lower() in SUB_EXTS:
                await msg.edit_text(
                    "📥 <b>Downloading subtitle…</b>", parse_mode=enums.ParseMode.HTML
                )
                sub_dest = os.path.join(work_dir, sub_fname)
                await client.download_media(message.document.file_id, file_name=sub_dest)
                if os.path.isfile(sub_dest):
                    external_sub = await _to_ass(sub_dest, work_dir)

        await msg.edit_text("⚙️ <b>Encoding…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(dest, message, msg, external_sub=external_sub)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Encode error:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ══════════════════════════════════════════════════════════════
#  /encurl — download URL and encode
# ══════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════
#  /encset and /vset
# ══════════════════════════════════════════════════════════════

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


@Client.on_message(filters.command("vset") & (filters.private | filters.group))
async def cmd_vset(client: Client, message: Message):
    if not await auth_required(message):
        return
    try:
        from bot.encoding.db import enc_db
        uid = message.from_user.id

        crf       = await enc_db.get_crf(uid)
        codec     = "H.265" if await enc_db.get_hevc(uid) else "H.264"
        preset    = await enc_db.get_preset(uid) or "sf"
        res       = await enc_db.get_resolution(uid) or "OG"
        audio     = await enc_db.get_audio(uid) or "aac"
        ext       = await enc_db.get_extensions(uid) or "MKV"
        hardsub   = "✅" if await enc_db.get_hardsub(uid) else "❌"
        softsub   = "✅" if await enc_db.get_subtitles(uid) else "❌"
        watermark = "✅" if await enc_db.get_watermark(uid) else "❌"

        await message.reply_text(
            "<b>⚙️ Your Encoding Settings</b>\n\n"
            f"🎬 <b>Codec:</b> <code>{codec}</code>\n"
            f"📹 <b>CRF:</b> <code>{crf}</code>\n"
            f"🚀 <b>Preset:</b> <code>{preset}</code>\n"
            f"📐 <b>Resolution:</b> <code>{'Source' if res == 'OG' else res + 'p'}</code>\n"
            f"🔊 <b>Audio:</b> <code>{audio.upper()}</code>\n"
            f"📄 <b>Output:</b> <code>{ext}</code>\n"
            f"📜 <b>Hardsub:</b> {hardsub}  <b>Softsub:</b> {softsub}\n"
            f"💧 <b>Watermark:</b> {watermark}\n\n"
            "Use /encset to change settings.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(
            f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
