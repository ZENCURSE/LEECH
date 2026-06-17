"""
encode.py — Encode command handlers  (full rewrite)
====================================================
Commands:
  /encode        reply to a video → encode it
  /encurl <url>  download URL → encode
  /encsub        interactive: send subtitle → send video → hardsub encode
  /encsub <v_url> <s_url>  both as URLs in one command
  /encset        open encode settings panel
  /vset          view current settings summary
"""
import asyncio
import os
import re
import time

from pyrogram import Client, filters, enums
from pyrogram.types import Message

import config
from bot.handlers._auth import auth_required
from bot.encoding.helper import handle_encode

# ── Constants ─────────────────────────────────────────────────
SUB_EXTS   = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m4v", ".flv", ".webm", ".wmv"}
_SEP       = "━━━━━━━━━━━━━━━━━━━━━━━━"

# ── /encsub step state ─────────────────────────────────────────
_state:   dict[int, dict]         = {}
_timers:  dict[int, asyncio.Task] = {}


def _cancel_state(uid: int):
    t = _timers.pop(uid, None)
    if t and not t.done():
        t.cancel()
    _state.pop(uid, None)


async def _expire(uid: int, msg, secs: int = 120):
    await asyncio.sleep(secs)
    if uid in _state:
        _state.pop(uid, None)
        try:
            await msg.edit_text(
                "⏰ <b>Timed out.</b> Send /encsub to start again.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


def _safe_name(fname: str) -> str:
    n = os.path.splitext(fname)[0]
    return "video" if re.match(r"^[0-9a-f]{20,}", n) else n[:48]


# ── Telegram download with live progress bar ───────────────────

async def _dl_tg(client, media, dest: str, msg, label: str) -> str:
    from bot.utils.progress import build_progress_card, safe_edit
    last  = [0.0]
    start = [time.monotonic()]

    async def _cb(cur, total):
        now = time.monotonic()
        if now - last[0] < 3.0:
            return
        elapsed = now - start[0]
        speed   = cur / max(elapsed, 0.001)
        eta     = (total - cur) / speed if speed > 0 and total > cur else 0
        await safe_edit(
            msg,
            build_progress_card(
                "downloading", label, cur * 100 / total if total else 0,
                done=cur, total=total, speed=speed, eta=eta, elapsed=elapsed,
            ),
        )
        last[0] = now

    fid = media.file_id if hasattr(media, "file_id") else media
    await client.download_media(fid, file_name=dest, progress=_cb)
    return dest


# ── SRT/VTT/SUB → ASS ────────────────────────────────────────

async def _to_ass(src: str, dest_dir: str) -> str:
    out  = os.path.join(dest_dir, os.path.splitext(os.path.basename(src))[0] + ".ass")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, out,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return out if os.path.isfile(out) else src


# ── Card helpers ──────────────────────────────────────────────

def _card(title: str, body: str) -> str:
    return (
        f"<b>{_SEP}</b>\n"
        f"<b>{title}</b>\n"
        f"<b>{_SEP}</b>\n\n"
        f"{body}"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /encode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@Client.on_message(filters.command("encode") & (filters.private | filters.group))
async def cmd_encode(client: Client, message: Message):
    if not await auth_required(message):
        return

    replied = message.reply_to_message
    if not replied or not (replied.video or replied.document):
        await message.reply_text(
            "❌ Reply to a video / document with <code>/encode</code>.\n\n"
            "For external subtitle → use <code>/encsub</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    msg = await message.reply_text(
        _card("📥  Downloading Video…", "Please wait…"),
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        media    = replied.video or replied.document
        fname    = getattr(media, "file_name", None) or f"video_{message.id}.mkv"
        work_dir = os.path.join(config.DOWNLOAD_DIR, f"enc_{message.id}")
        os.makedirs(work_dir, exist_ok=True)
        dest     = os.path.join(work_dir, fname)

        await _dl_tg(client, media, dest, msg, _safe_name(fname))

        if not os.path.isfile(dest):
            return await msg.edit_text("❌ Download failed.", parse_mode=enums.ParseMode.HTML)

        # Optional subtitle attached directly to /encode message
        external_sub = None
        if message.document:
            sfname = message.document.file_name or ""
            if os.path.splitext(sfname)[1].lower() in SUB_EXTS:
                await msg.edit_text(
                    _card("📥  Downloading Subtitle…", "Please wait…"),
                    parse_mode=enums.ParseMode.HTML,
                )
                sdest = os.path.join(work_dir, sfname)
                await _dl_tg(client, message.document, sdest, msg, _safe_name(sfname))
                if os.path.isfile(sdest):
                    external_sub = await _to_ass(sdest, work_dir)

        await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(dest, message, msg, external_sub=external_sub)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /encurl
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@Client.on_message(filters.command("encurl") & (filters.private | filters.group))
async def cmd_encurl(client: Client, message: Message):
    if not await auth_required(message):
        return

    parts = message.text.split(None, 2)
    if len(parts) < 2:
        return await message.reply_text(
            "❌ Usage: <code>/encurl &lt;url&gt; [filename]</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    url      = parts[1].strip()
    filename = parts[2].strip() if len(parts) > 2 else None
    msg      = await message.reply_text(
        _card("📥  Downloading…", f"<code>{url[:80]}</code>"),
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        from bot.utils.direct_links import resolve
        from bot.downloaders.ytdlp_downloader import ytdlp_download
        from bot.downloaders.http_downloader  import http_download

        uid      = message.from_user.id
        dest_dir = os.path.join(config.DOWNLOAD_DIR, f"encurl_{message.id}")
        os.makedirs(dest_dir, exist_ok=True)
        tid      = f"encurl_{message.id}"

        info = await resolve(url)
        if info["use_ytdlp"]:
            filepath = await ytdlp_download(info["url"], dest_dir, tid, msg, uid)
        else:
            filepath = await http_download(info["url"], dest_dir, tid, msg)

        if filename:
            ext      = os.path.splitext(filepath)[1]
            new_path = os.path.join(dest_dir, filename + ext)
            os.rename(filepath, new_path)
            filepath = new_path

        await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
        await handle_encode(filepath, message, msg, tid=tid)

    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /encsub  (interactive 2-step: subtitle first → video)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@Client.on_message(filters.command("encsub") & (filters.private | filters.group))
async def cmd_encsub(client: Client, message: Message):
    if not await auth_required(message):
        return

    uid   = message.from_user.id
    parts = message.text.split(None, 3)

    # ── URL mode: /encsub <video_url> <sub_url> ───────────────
    if len(parts) >= 3:
        video_url = parts[1].strip()
        sub_url   = parts[2].strip()
        msg = await message.reply_text(
            _card("📥  Downloading Video…", f"<code>{video_url[:80]}</code>"),
            parse_mode=enums.ParseMode.HTML,
        )
        try:
            from bot.utils.direct_links import resolve
            from bot.downloaders.ytdlp_downloader import ytdlp_download
            from bot.downloaders.http_downloader  import http_download

            uid_str  = str(message.from_user.id)
            work_dir = os.path.join(config.DOWNLOAD_DIR, f"encsub_{message.id}")
            os.makedirs(work_dir, exist_ok=True)
            tid      = f"encsub_{message.id}"

            info = await resolve(video_url)
            if info["use_ytdlp"]:
                vpath = await ytdlp_download(info["url"], work_dir, tid, msg, message.from_user.id)
            else:
                vpath = await http_download(info["url"], work_dir, tid, msg)

            await msg.edit_text(
                _card("📥  Downloading Subtitle…", f"<code>{sub_url[:80]}</code>"),
                parse_mode=enums.ParseMode.HTML,
            )
            spath    = await http_download(sub_url, work_dir, tid + "_s", msg)
            sub_ass  = await _to_ass(spath, work_dir)

            await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
            await handle_encode(vpath, message, msg, external_sub=sub_ass, tid=tid)
        except Exception as e:
            await msg.edit_text(
                f"❌ <b>Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
            )
        return

    # ── Interactive mode: ask for subtitle ────────────────────
    _cancel_state(uid)
    work_dir = os.path.join(config.DOWNLOAD_DIR, f"encsub_{uid}_{message.id}")
    os.makedirs(work_dir, exist_ok=True)
    _state[uid] = {"step": "await_sub", "work_dir": work_dir}

    prompt = await message.reply_text(
        _card(
            "📄  Step 1 / 2 — Send Subtitle",
            "Send your subtitle file.\n"
            "Supported: <code>.srt  .ass  .ssa  .vtt  .sub</code>\n\n"
            "<i>⏳ Waiting 2 minutes…</i>",
        ),
        parse_mode=enums.ParseMode.HTML,
    )
    _timers[uid] = asyncio.ensure_future(_expire(uid, prompt, 120))


# ── Step 1: receive subtitle ──────────────────────────────────

@Client.on_message(filters.document & (filters.private | filters.group), group=3)
async def encsub_step1_sub(client: Client, message: Message):
    if not message.from_user:
        return
    uid   = message.from_user.id
    state = _state.get(uid)
    if not state or state["step"] != "await_sub":
        return

    doc   = message.document
    fname = doc.file_name or "subtitle.srt"
    ext   = os.path.splitext(fname)[1].lower()

    if ext not in SUB_EXTS:
        return await message.reply_text(
            f"❌ Unsupported format <code>{ext}</code>\n"
            f"Accepted: {', '.join(sorted(SUB_EXTS))}",
            parse_mode=enums.ParseMode.HTML,
        )

    _cancel_state(uid)
    work_dir = state["work_dir"]
    _state[uid] = {
        "step":      "await_video",
        "work_dir":  work_dir,
        "sub_fid":   doc.file_id,
        "sub_fname": fname,
    }

    msg = await message.reply_text(
        _card(
            "✅  Subtitle received!  |  Step 2 / 2 — Send Video",
            f"📄 <code>{os.path.splitext(fname)[0]}</code>\n\n"
            "Now send the video file to encode.\n"
            "Supported: <code>.mkv  .mp4  .avi  .mov  .ts</code>\n\n"
            "<i>⏳ Waiting 2 minutes…</i>",
        ),
        parse_mode=enums.ParseMode.HTML,
    )
    _state[uid]["msg"] = msg
    _timers[uid] = asyncio.ensure_future(_expire(uid, msg, 120))


# ── Step 2: receive video → download both → encode ────────────

@Client.on_message(
    (filters.video | filters.document) & (filters.private | filters.group),
    group=4,
)
async def encsub_step2_video(client: Client, message: Message):
    if not message.from_user:
        return
    uid   = message.from_user.id
    state = _state.get(uid)
    if not state or state["step"] != "await_video":
        return

    media = message.video or message.document
    if not media:
        return
    fname = getattr(media, "file_name", None) or "video.mkv"
    ext   = os.path.splitext(fname)[1].lower()
    if message.document and ext not in VIDEO_EXTS:
        return   # not a video doc — ignore

    _cancel_state(uid)
    work_dir  = state["work_dir"]
    sub_fid   = state["sub_fid"]
    sub_fname = state["sub_fname"]
    msg       = state.get("msg")
    _state.pop(uid, None)

    if not msg:
        msg = await message.reply_text("⚙️ Processing…", parse_mode=enums.ParseMode.HTML)

    vname = _safe_name(fname)
    sname = os.path.splitext(sub_fname)[0]

    await msg.edit_text(
        _card(
            "📥  Downloading Both Files…",
            f"🎬 <code>{vname}</code>\n📄 <code>{sname}</code>",
        ),
        parse_mode=enums.ParseMode.HTML,
    )

    vdest = os.path.join(work_dir, fname)
    sdest = os.path.join(work_dir, sub_fname)

    await asyncio.gather(
        client.download_media(media.file_id, file_name=vdest),
        client.download_media(sub_fid,       file_name=sdest),
    )

    if not os.path.isfile(vdest):
        return await msg.edit_text("❌ Video download failed.", parse_mode=enums.ParseMode.HTML)
    if not os.path.isfile(sdest):
        return await msg.edit_text("❌ Subtitle download failed.", parse_mode=enums.ParseMode.HTML)

    sub_ass = await _to_ass(sdest, work_dir)

    await msg.edit_text("⚙️ <b>Starting encode…</b>", parse_mode=enums.ParseMode.HTML)
    tid = f"encsub_{uid}_{message.id}"
    await handle_encode(vdest, message, msg, external_sub=sub_ass, tid=tid)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  /encset  /vset
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
        await message.reply_text(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("vset") & (filters.private | filters.group))
async def cmd_vset(client: Client, message: Message):
    if not await auth_required(message):
        return
    try:
        from bot.encoding.db import enc_db
        uid = message.from_user.id
        preset_map = {
            "uf": "ultrafast", "sf": "superfast", "vf": "veryfast",
            "f": "fast", "m": "medium", "s": "slow",
        }
        codec     = "H.265 (HEVC)" if await enc_db.get_hevc(uid)    else "H.264 (AVC)"
        crf       = await enc_db.get_crf(uid)     or 26
        preset    = preset_map.get(await enc_db.get_preset(uid), "slow")
        res_raw   = await enc_db.get_resolution(uid) or "OG"
        res       = "Source" if res_raw == "OG" else f"{res_raw}p"
        audio     = (await enc_db.get_audio(uid)     or "aac").upper()
        ext       = await enc_db.get_extensions(uid) or "MKV"
        hardsub   = "✅" if await enc_db.get_hardsub(uid)   else "❌"
        softsub   = "✅" if await enc_db.get_subtitles(uid) else "❌"
        watermark = "✅" if await enc_db.get_watermark(uid) else "❌"
        bits_raw  = await enc_db.get_bits(uid)
        bits      = "10-bit" if bits_raw else "8-bit"

        await message.reply_text(
            f"<b>{_SEP}</b>\n"
            f"<b>⚙️  Current Encoding Settings</b>\n"
            f"<b>{_SEP}</b>\n\n"
            f"🎬  <b>Codec</b>       :  <code>{codec}</code>\n"
            f"🎚  <b>CRF</b>         :  <code>{crf}</code>\n"
            f"🚀  <b>Preset</b>      :  <code>{preset}</code>\n"
            f"📐  <b>Resolution</b>  :  <code>{res}</code>\n"
            f"🔊  <b>Audio</b>       :  <code>{audio}</code>\n"
            f"🎨  <b>Bit depth</b>   :  <code>{bits}</code>\n"
            f"📄  <b>Container</b>   :  <code>{ext}</code>\n"
            f"📜  <b>Hardsub</b>     :  {hardsub}   "
            f"<b>Softsub</b> :  {softsub}\n"
            f"💧  <b>Watermark</b>   :  {watermark}\n\n"
            f"<b>{_SEP}</b>\n"
            f"Use /encset to change settings.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
