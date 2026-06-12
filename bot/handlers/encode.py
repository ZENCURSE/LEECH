"""
Encode handler — NXT_HUB v5

Step order for /encsub:
  1. /encsub → bot asks for video
  2. User sends video → bot downloads WITH progress bar → asks for subtitle
  3. User sends subtitle → bot downloads subtitle → converts → encodes WITH progress → uploads

Commands:
  /encode        — reply to a video file to encode it
  /encurl <url>  — download URL and encode
  /encsub        — interactive: video first, then subtitle, then encode
  /encsub <v_url> <s_url> — both as URLs
  /encset        — open encoding settings panel
  /vset          — view current encode settings
"""
import os
import re
import time
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import config
from bot.handlers._auth import auth_required
from bot.database import users_db
from bot.encoding.helper import handle_encode

# ── State ─────────────────────────────────────────────────────
_encsub_state:   dict[int, dict]         = {}
_encsub_timeout: dict[int, asyncio.Task] = {}

SUB_EXTS   = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m4v", ".flv", ".webm", ".wmv"}
SEP        = "━━━━━━━━━━━━━━━━━━━━━━━━"


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
                "⏰ <b>Timed out.</b> Send /encsub to start again.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


def _clean_name(fname: str) -> str:
    """Strip UUID-style names from Telegram file hashes."""
    name = os.path.splitext(fname)[0]
    if re.match(r'^[0-9a-f]{20,}', name):
        return "video"
    return name[:40]


# ── Download from Telegram with live progress bar ─────────────

async def _dl_tg_with_progress(client, file_id: str, dest: str, msg, label: str) -> str:
    """
    Download a Telegram file to dest, showing a live progress bar
    on msg that updates every 2 seconds.
    """
    SEP = "━━━━━━━━━━━━━━━━━━━━━━━━"
    last_edit  = [0.0]
    last_bytes = [0]
    start_time = [time.monotonic()]

    def _human_size(b):
        for u in ("B", "KB", "MB", "GB"):
            if b < 1024: return f"{b:.1f} {u}"
            b /= 1024
        return f"{b:.1f} GB"

    def _human_speed(bps):
        if bps < 1024:       return f"{bps:.0f} B/s"
        if bps < 1024**2:    return f"{bps/1024:.1f} KB/s"
        return f"{bps/1024**2:.1f} MB/s"

    async def _progress(current, total):
        now    = time.monotonic()
        if now - last_edit[0] < 2.0:
            return
        elapsed = now - start_time[0]
        speed   = current / max(elapsed, 0.001)
        eta     = int((total - current) / speed) if speed > 0 and total > current else 0
        pct     = int(current * 100 / total) if total else 0
        filled  = int(pct / 10)
        bar     = "█" * filled + "░" * (10 - filled)
        mm, ss  = divmod(eta, 60)
        eta_str = f"{mm}m {ss}s" if mm else f"{ss}s"

        try:
            await msg.edit_text(
                f"<b>{SEP}</b>\n"
                f"<b>📥  {label}</b>\n"
                f"<b>{SEP}</b>\n\n"
                f"<b><code>{bar}</code>  {pct}%</b>\n\n"
                f"📦 <b>{_human_size(current)}</b> / <b>{_human_size(total)}</b>\n"
                f"⚡ <b>{_human_speed(speed)}</b>\n"
                f"🕐 <b>ETA: {eta_str}</b>\n\n"
                f"<b>{SEP}</b>\n"
                f"<b>⚡ {config.WATERMARK}</b>",
                parse_mode=enums.ParseMode.HTML,
            )
            last_edit[0] = now
        except Exception:
            pass

    await client.download_media(file_id, file_name=dest, progress=_progress)
    return dest


# ── SRT/VTT → ASS conversion ──────────────────────────────────

async def _to_ass(src: str, dest_dir: str) -> str:
    name = os.path.splitext(os.path.basename(src))[0]
    out  = os.path.join(dest_dir, name + ".ass")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", src, out,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return out if os.path.isfile(out) else src


# ══════════════════════════════════════════════════════════════
#  /encsub
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
            f"<b>{SEP}</b>\n<b>📥  Downloading Video…</b>\n<b>{SEP}</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        try:
            from bot.core.downloader import http_download, ytdlp_download
            from bot.utils.direct_links import resolve

            work_dir = os.path.join(config.DOWNLOAD_DIR, f"encsub_{message.id}")
            os.makedirs(work_dir, exist_ok=True)

            info = await resolve(video_url)
            tid  = f"encsub_v_{message.id}"
            if info["use_ytdlp"]:
                video_path = await ytdlp_download(info["url"], work_dir, tid, msg, uid)
            else:
                video_path = await http_download(info["url"], work_dir, tid, msg)

            await msg.edit_text(
                f"<b>{SEP}</b>\n<b>📥  Downloading Subtitle…</b>\n<b>{SEP}</b>",
                parse_mode=enums.ParseMode.HTML,
            )
            sub_path = await http_download(sub_url, work_dir, f"encsub_s_{message.id}", msg)
            sub_path = await _to_ass(sub_path, work_dir)

            await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
            await handle_encode(video_path, message, msg, external_sub=sub_path)

        except Exception as e:
            await msg.edit_text(
                f"❌ <b>Error:</b>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        return

    # ── Flow A: step-by-step ───────────────────────────────────
    _cancel_encsub(uid)
    work_dir = os.path.join(config.DOWNLOAD_DIR, f"encsub_{uid}_{message.id}")
    os.makedirs(work_dir, exist_ok=True)
    _encsub_state[uid] = {"step": "video", "work_dir": work_dir}

    prompt = await message.reply_text(
        f"<b>{SEP}</b>\n"
        f"<b>🎬  Step 1 / 2 — Send Video</b>\n"
        f"<b>{SEP}</b>\n\n"
        f"Send the video file you want to encode.\n"
        f"Supported: <code>.mkv  .mp4  .avi  .mov  .ts</code>\n\n"
        f"<i>⏳ Waiting 2 minutes…</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    _encsub_timeout[uid] = asyncio.ensure_future(_encsub_expire(uid, prompt, 120))


# ── Step 1: receive video ─────────────────────────────────────

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

    fname = getattr(media, "file_name", None) or "video.mkv"
    ext   = os.path.splitext(fname)[1].lower()

    # If document, check it's a video extension not a subtitle sent early
    if message.document and ext not in VIDEO_EXTS:
        return

    _cancel_encsub(uid)
    work_dir   = state["work_dir"]
    clean_name = _clean_name(fname)

    # Show download progress card
    msg = await message.reply_text(
        f"<b>{SEP}</b>\n<b>📥  Downloading Video…</b>\n<b>{SEP}</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    dest = os.path.join(work_dir, fname)
    await _dl_tg_with_progress(client, media.file_id, dest, msg, "DOWNLOADING VIDEO")

    if not os.path.isfile(dest):
        await msg.edit_text("❌ Video download failed.", parse_mode=enums.ParseMode.HTML)
        _encsub_state.pop(uid, None)
        return

    _encsub_state[uid] = {"step": "sub", "video_path": dest, "work_dir": work_dir, "msg": msg}

    # Ask for subtitle
    await msg.edit_text(
        f"<b>{SEP}</b>\n"
        f"<b>✅  Video downloaded!</b>\n"
        f"<b>{SEP}</b>\n\n"
        f"🎬 <code>{clean_name}</code>\n\n"
        f"<b>📄  Step 2 / 2 — Send Subtitle</b>\n\n"
        f"Send your subtitle file.\n"
        f"Supported: <code>.srt  .ass  .ssa  .vtt  .sub</code>\n\n"
        f"<i>⏳ Waiting 2 minutes…</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    _encsub_timeout[uid] = asyncio.ensure_future(_encsub_expire(uid, msg, 120))


# ── Step 2: receive subtitle ──────────────────────────────────

@Client.on_message(filters.document & (filters.private | filters.group), group=4)
async def encsub_receive_sub(client: Client, message: Message):
    if not message.from_user:
        return
    uid   = message.from_user.id
    state = _encsub_state.get(uid)
    if not state or state["step"] != "sub":
        return

    doc   = message.document
    fname = doc.file_name or "subtitle.srt"
    ext   = os.path.splitext(fname)[1].lower()

    if ext not in SUB_EXTS:
        await message.reply_text(
            f"❌ Unsupported format <code>{ext}</code>\n"
            f"Accepted: {' '.join(sorted(SUB_EXTS))}",
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

    # Download subtitle with progress
    await msg.edit_text(
        f"<b>{SEP}</b>\n<b>📥  Downloading Subtitle…</b>\n<b>{SEP}</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    sub_dest = os.path.join(work_dir, fname)
    await _dl_tg_with_progress(client, doc.file_id, sub_dest, msg, "DOWNLOADING SUBTITLE")

    if not os.path.isfile(sub_dest):
        await msg.edit_text("❌ Subtitle download failed.", parse_mode=enums.ParseMode.HTML)
        return

    # Convert to .ass
    sub_ass = await _to_ass(sub_dest, work_dir)

    video_name = _clean_name(os.path.basename(video_path))
    sub_name   = os.path.splitext(fname)[0]

    await msg.edit_text(
        f"<b>{SEP}</b>\n"
        f"<b>⚙️  Starting Encode</b>\n"
        f"<b>{SEP}</b>\n\n"
        f"🎬 <code>{video_name}</code>\n"
        f"📄 <code>{sub_name}</code>\n\n"
        f"<i>Hardsub will be burned in…</i>",
        parse_mode=enums.ParseMode.HTML,
    )

    await handle_encode(video_path, message, msg, external_sub=sub_ass)


# ══════════════════════════════════════════════════════════════
#  /encode — reply to video (optionally with subtitle attached)
# ══════════════════════════════════════════════════════════════

@Client.on_message(filters.command("encode") & (filters.private | filters.group))
async def cmd_encode(client: Client, message: Message):
    if not await auth_required(message):
        return

    replied = message.reply_to_message
    if not (replied and (replied.video or replied.document)):
        await message.reply_text(
            "❌ Reply to a video/document with <code>/encode</code>.\n\n"
            "To use an external subtitle: <code>/encsub</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    msg = await message.reply_text(
        f"<b>{SEP}</b>\n<b>📥  Downloading Video…</b>\n<b>{SEP}</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        media      = replied.video or replied.document
        fname      = getattr(media, "file_name", None) or f"video_{message.id}.mkv"
        work_dir   = os.path.join(config.DOWNLOAD_DIR, f"enc_{message.id}")
        os.makedirs(work_dir, exist_ok=True)
        dest       = os.path.join(work_dir, fname)

        await _dl_tg_with_progress(client, replied, dest, msg, "DOWNLOADING VIDEO")

        if not os.path.isfile(dest):
            await msg.edit_text("❌ Download failed.", parse_mode=enums.ParseMode.HTML)
            return

        # Check if subtitle attached to /encode message itself
        external_sub = None
        if message.document:
            sub_fname = message.document.file_name or ""
            if os.path.splitext(sub_fname)[1].lower() in SUB_EXTS:
                await msg.edit_text(
                    f"<b>{SEP}</b>\n<b>📥  Downloading Subtitle…</b>\n<b>{SEP}</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
                sub_dest = os.path.join(work_dir, sub_fname)
                await _dl_tg_with_progress(client, message.document.file_id, sub_dest, msg, "DOWNLOADING SUBTITLE")
                if os.path.isfile(sub_dest):
                    external_sub = await _to_ass(sub_dest, work_dir)

        await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(dest, message, msg, external_sub=external_sub)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Encode error:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ══════════════════════════════════════════════════════════════
#  /encurl
# ══════════════════════════════════════════════════════════════

@Client.on_message(filters.command("encurl") & (filters.private | filters.group))
async def cmd_encurl(client: Client, message: Message):
    if not await auth_required(message):
        return

    parts = message.text.split(None, 2)
    if len(parts) < 2:
        await message.reply_text(
            "❌ Usage: <code>/encurl &lt;url&gt; [filename]</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    url      = parts[1].strip()
    filename = parts[2].strip() if len(parts) > 2 else None

    msg = await message.reply_text(
        f"<b>{SEP}</b>\n<b>📥  Downloading…</b>\n<b>{SEP}</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        from bot.core.downloader import http_download, ytdlp_download
        from bot.utils.direct_links import resolve

        dest_dir = os.path.join(config.DOWNLOAD_DIR, f"enc_url_{message.id}")
        os.makedirs(dest_dir, exist_ok=True)

        info = await resolve(url)
        tid  = f"encurl_{message.id}"
        if info["use_ytdlp"]:
            filepath = await ytdlp_download(info["url"], dest_dir, tid, msg, message.from_user.id)
        else:
            filepath = await http_download(info["url"], dest_dir, tid, msg)

        if filename:
            ext      = os.path.splitext(filepath)[1]
            new_path = os.path.join(dest_dir, filename + ext)
            os.rename(filepath, new_path)
            filepath = new_path

        await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(filepath, message, msg)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Error:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ══════════════════════════════════════════════════════════════
#  /encset  /vset
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
            f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.command("vset") & (filters.private | filters.group))
async def cmd_vset(client: Client, message: Message):
    if not await auth_required(message):
        return
    try:
        from bot.encoding.db import enc_db
        uid = message.from_user.id
        codec     = "H.265" if await enc_db.get_hevc(uid) else "H.264"
        crf       = await enc_db.get_crf(uid)
        preset_map = {"uf":"ultrafast","sf":"superfast","vf":"veryfast","f":"fast","m":"medium","s":"slow"}
        preset    = preset_map.get(await enc_db.get_preset(uid), "slow")
        res       = await enc_db.get_resolution(uid) or "OG"
        audio     = (await enc_db.get_audio(uid) or "aac").upper()
        ext       = await enc_db.get_extensions(uid) or "MKV"
        hardsub   = "✅" if await enc_db.get_hardsub(uid) else "❌"
        softsub   = "✅" if await enc_db.get_subtitles(uid) else "❌"
        watermark = "✅" if await enc_db.get_watermark(uid) else "❌"
        await message.reply_text(
            f"<b>{SEP}</b>\n<b>⚙️  Encoding Settings</b>\n<b>{SEP}</b>\n\n"
            f"🎬 <b>Codec:</b> <code>{codec}</code>\n"
            f"📹 <b>CRF:</b> <code>{crf}</code>\n"
            f"🚀 <b>Preset:</b> <code>{preset}</code>\n"
            f"📐 <b>Resolution:</b> <code>{'Source' if res=='OG' else res+'p'}</code>\n"
            f"🔊 <b>Audio:</b> <code>{audio}</code>\n"
            f"📄 <b>Output:</b> <code>{ext}</code>\n"
            f"📜 <b>Hardsub:</b> {hardsub}   <b>Softsub:</b> {softsub}\n"
            f"💧 <b>Watermark:</b> {watermark}\n\n"
            f"<b>{SEP}</b>\n"
            f"Use /encset to change.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
