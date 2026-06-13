"""
Uploader — NXT_HUB v5

Key fixes:
  - Filename is CLEAN — no tokens ever embedded in it
  - Token info block sent as a SEPARATE quoted reply after the file
  - Telegram quoted reply used via reply_to_message_id + quote parameter
  - Watermark removed from file caption entirely
"""
from pyrogram import enums
import os, math, time, asyncio, aiofiles, re
import config

from pyrogram.errors import FloodWait, BadRequest, RPCError
from bot.core import task_manager as tm
from bot.utils.progress import done_card, task_kb
from bot.utils.size_utils import human_size
from bot.utils.thumbnail import get_thumbnail
from bot.utils.rename import smart_rename, parse_title_year
from bot.utils.token_resolver import (
    resolve_tokens, _ffprobe,
    _tok_size, _tok_name, _tok_ext, _tok_date,
    _tok_language, _tok_time, _tok_quality,
    _tok_codec, _tok_audio, _tok_fps,
    _ASYNC_TOKENS,
)
from bot.utils.media_utils import (
    get_document_type, get_media_info,
    get_video_thumbnail, get_audio_thumbnail,
    _hq_resize_thumb,
)
from bot.database import users_db

CHUNK          = 512 * 1024
_2GB           = 2 * 1024 * 1024 * 1024   # 2 GB — standard Telegram limit
_4GB           = 4 * 1024 * 1024 * 1024   # 4 GB — Premium session limit


def _split_size() -> int:
    """
    Return the correct split size based on whether a premium user session is
    configured. Without SESSION: 2 GB. With SESSION: 4 GB.
    Files larger than this are automatically split into numbered parts.
    """
    return _4GB if config.SESSION else _2GB

# Tokens that belong in the info block — never go into filename
_INFO_TOKENS = frozenset({"size", "language", "time", "quality", "codec", "audio", "fps", "ext", "date"})
_TOKEN_RE    = re.compile(r"\{(\w+)\}")


async def _split_file(path, part_size):
    total = os.path.getsize(path)
    n     = math.ceil(total / part_size)
    base, ext = os.path.splitext(path)
    # Use .partNN.ext so Telegram doesn't reject unknown extensions
    # but also store as .bin to force document mode (avoids size-limit on media)
    parts = []
    async with aiofiles.open(path, "rb") as src:
        for i in range(n):
            # Always upload split parts as documents (.bin avoids video size checks)
            pp   = f"{base}.part{i+1:02d}{ext}"
            left = part_size
            async with aiofiles.open(pp, "wb") as dst:
                while left > 0:
                    chunk = await src.read(min(CHUNK, left))
                    if not chunk: break
                    await dst.write(chunk); left -= len(chunk)
            parts.append(pp)
    return parts


async def _resolve_thumb(uid, name, tmp_dir, title: str = ""):
    """
    Resolve thumbnail for upload.

    Priority:
      1. User-set custom thumb (thumb_path in settings)
      2. Auto-fetched from TMDB/Fanart via title lookup

    Both paths are processed through _hq_resize_thumb (1280×720, JPEG q=95,
    subsampling=0) before being returned. This prevents Telegram from
    recompressing a low-quality or oversized image on its end — the server
    only recompresses if the file it receives is already poor quality or
    wrong dimensions.
    """
    try:
        s = users_db.get_settings(uid)

        if s.get("thumb_path") and os.path.exists(s["thumb_path"]):
            src    = s["thumb_path"]
            resized = os.path.join(tmp_dir, f"thumb_hq_{uid}.jpg")
            return _hq_resize_thumb(src, resized, max_w=1280, max_h=720)

        from bot.utils.rename import parse_title_year
        t, year = parse_title_year(name)
        lookup_title = t or title or name
        if lookup_title:
            dest = os.path.join(tmp_dir, f"auto_thumb_{uid}.jpg")
            from bot.utils.thumbnail import get_thumbnail
            if await get_thumbnail(lookup_title, year, dest, title=lookup_title):
                # thumbnail.py already saves at q=95 but may be >1280px wide
                resized = os.path.join(tmp_dir, f"auto_thumb_hq_{uid}.jpg")
                return _hq_resize_thumb(dest, resized, max_w=1280, max_h=720)
    except Exception:
        pass
    return None


# ── Filename builder — strips ALL info tokens, keeps only literal + {name} ──
async def _build_filename(orig_name: str, prefix_tpl: str, suffix_tpl: str,
                          file_path: str) -> str:
    """
    Build the clean filename using clean_name() for full sanitisation:
      - Strips site watermarks, codec tags, separators from actual filename
      - {name} in prefix/suffix → replaced with clean stem
      - Info tokens ({size} etc.) → stripped from filename (go in caption instead)
    """
    from bot.utils.rename import clean_name
    # clean_name does full sanitisation: sites, codecs, separators
    cleaned     = clean_name(orig_name)
    stem, ext   = os.path.splitext(cleaned)

    def _clean_tpl(tpl: str) -> str:
        if not tpl:
            return ""
        out = tpl.replace("{name}", stem)
        out = _TOKEN_RE.sub(
            lambda m: "" if m.group(1) in _INFO_TOKENS else m.group(0),
            out,
        )
        return out.strip()

    prefix_clean = _clean_tpl(prefix_tpl)
    suffix_clean = _clean_tpl(suffix_tpl)

    # Assemble: [prefix] stem [suffix]
    parts = [p for p in [prefix_clean, stem, suffix_clean] if p]
    return " ".join(parts) + ext


# ── Token info block — separate message sent as Telegram quoted reply ────────
_TOKEN_ICONS = {
    "size":     ("📦", "Size"),
    "language": ("🌐", "Language"),
    "time":     ("⏱", "Duration"),
    "quality":  ("🎬", "Quality"),
    "codec":    ("🎞", "Video"),
    "audio":    ("🔊", "Audio"),
    "fps":      ("⚡", "FPS"),
    "ext":      ("📄", "Format"),
    "date":     ("📅", "Date"),
}

async def _build_token_block(prefix_tpl: str, suffix_tpl: str, file_path: str) -> str | None:
    """
    Build a token info block string if the user has any info tokens in their
    prefix/suffix. Returns None if no info tokens are used.

    Format (sent as a quoted reply to the uploaded file):
      📦 Size: 1.4 GB
      🌐 Language: Hindi, English
      ⏱ Duration: 2:15:30
      🎬 Quality: 1080p
      🎞 Video: H.265
      🔊 Audio: DTS
    """
    combined = (prefix_tpl or "") + (suffix_tpl or "")
    used = {m for m in _TOKEN_RE.findall(combined) if m in _INFO_TOKENS}
    if not used:
        return None

    # Run ffprobe only if needed
    data = await _ffprobe(file_path) if used & _ASYNC_TOKENS else {}

    lines = []
    for tok in ["size", "language", "time", "quality", "codec", "audio", "fps", "ext", "date"]:
        if tok not in used:
            continue
        if tok == "size":     val = _tok_size(file_path)
        elif tok == "language": val = await _tok_language(file_path, data)
        elif tok == "time":   val = await _tok_time(file_path, data)
        elif tok == "quality": val = await _tok_quality(file_path, data)
        elif tok == "codec":  val = await _tok_codec(file_path, data)
        elif tok == "audio":  val = await _tok_audio(file_path, data)
        elif tok == "fps":    val = await _tok_fps(file_path, data)
        elif tok == "ext":    val = _tok_ext(file_path).upper()
        elif tok == "date":   val = _tok_date()
        else: continue

        if val and val != "?":
            icon, label = _TOKEN_ICONS[tok]
            lines.append(f"{icon} <b>{label}:</b>  {val}")

    if not lines:
        return None

    return "\n".join(lines)


# ── Thumbnail helpers ─────────────────────────────────────────

async def _get_thumb_dims(thumb_path: str) -> tuple[int, int]:
    """
    Get actual pixel dimensions of a thumbnail file.
    Returns (width, height). Falls back to (1280, 720) if unreadable.
    Passing correct dims to send_video/send_document ensures Telegram
    displays the full-resolution thumbnail instead of resampling it.
    """
    if not thumb_path or not os.path.isfile(thumb_path):
        return 1280, 720
    try:
        from PIL import Image
        with Image.open(thumb_path) as img:
            return img.size  # (width, height)
    except Exception:
        pass
    # Fallback: ffprobe
    try:
        import subprocess, json
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json", thumb_path
        ], timeout=5).decode()
        s = json.loads(out)["streams"][0]
        return int(s["width"]), int(s["height"])
    except Exception:
        return 1280, 720


def _prep_thumb(thumb_path: str) -> str | None:
    """
    Ensure thumbnail is:
    - JPEG format
    - Quality 95, subsampling=0 (4:4:4 chroma — no colour smearing)
    - Max 1280x720 (never upscale)
    Returns path to processed thumb, or None.
    Telegram MTProto (pyrofork) accepts larger than 320px — the 320px
    limit only applies to the HTTP Bot API. Via MTProto, Telegram stores
    and serves the full image as-is.
    """
    if not thumb_path or not os.path.isfile(thumb_path):
        return None
    try:
        from PIL import Image
        with Image.open(thumb_path) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            # Cap at 1280x720 keeping aspect, never upscale
            scale = min(1280 / w, 720 / h, 1.0)
            if scale < 1.0:
                rgb = rgb.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            # Save to temp file alongside original
            out = thumb_path.rsplit(".", 1)[0] + "_hd.jpg"
            rgb.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
            return out
    except Exception:
        return thumb_path


# ── Send helpers ──────────────────────────────────────────────

async def _send_doc(client, chat_id, path, caption, thumb, cb):
    hd_thumb = _prep_thumb(thumb)
    tw, th = await _get_thumb_dims(hd_thumb) if hd_thumb else (None, None)
    return await client.send_document(
        chat_id=chat_id, document=path, thumb=hd_thumb,
        caption=caption, parse_mode=enums.ParseMode.HTML,
        disable_notification=True, progress=cb,
    )


async def _send(client, chat_id, path, caption, thumb, cb, as_doc):
    is_video, is_audio, is_image = await get_document_type(path)

    if as_doc or (not is_video and not is_audio and not is_image):
        if is_video and not thumb:
            thumb = await get_video_thumbnail(path, None)
        return await _send_doc(client, chat_id, path, caption, thumb, cb)

    if is_video:
        duration, _, _ = await get_media_info(path)
        if not thumb:
            thumb = await get_video_thumbnail(path, duration)
        hd_thumb = _prep_thumb(thumb)
        tw, th    = await _get_thumb_dims(hd_thumb) if hd_thumb else (1280, 720)
        # Also read actual video dimensions to pass correct w/h
        try:
            import subprocess, json as _json
            _out = subprocess.check_output([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json", path
            ], timeout=5).decode()
            _s = _json.loads(_out)["streams"][0]
            vw, vh = int(_s["width"]), int(_s["height"])
        except Exception:
            vw, vh = tw, th
        try:
            return await client.send_video(
                chat_id=chat_id, video=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, duration=duration or 0,
                width=vw, height=vh,
                thumb=hd_thumb, supports_streaming=True,
                disable_notification=True, progress=cb,
            )
        except (BadRequest, RPCError):
            return await _send_doc(client, chat_id, path, caption, hd_thumb, cb)

    if is_audio:
        duration, artist, title = await get_media_info(path)
        if not thumb: thumb = await get_audio_thumbnail(path)
        hd_thumb = _prep_thumb(thumb)
        try:
            return await client.send_audio(
                chat_id=chat_id, audio=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, duration=duration or 0,
                performer=artist or "",
                title=title or os.path.splitext(os.path.basename(path))[0],
                thumb=hd_thumb, disable_notification=True, progress=cb,
            )
        except (BadRequest, RPCError):
            return await _send_doc(client, chat_id, path, caption, hd_thumb, cb)

    if is_image:
        try:
            return await client.send_photo(
                chat_id=chat_id, photo=path, caption=caption,
                parse_mode=enums.ParseMode.HTML,
                disable_notification=True, progress=cb,
            )
        except (BadRequest, RPCError):
            return await _send_doc(client, chat_id, path, caption, thumb, cb)

    return await _send_doc(client, chat_id, path, caption, thumb, cb)


# ── Main upload function ──────────────────────────────────────
async def upload_file(client, chat_id: int, file_path: str,
                      task_id: str, msg, uid: int,
                      origin_msg=None, is_group: bool = False,
                      progress_msg=None) -> None:
    """
    progress_msg: if provided (encode flow), upload progress is edited
                  directly on this message rather than via status loop.
    """

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Not found: {file_path}")

    s       = users_db.get_settings(uid)
    as_doc  = s.get("as_doc", config.AS_DOCUMENT)
    prefix  = s.get("prefix", "") or ""
    suffix  = s.get("suffix", "") or ""
    tmp_dir = os.path.dirname(file_path)

    # Apply rename regex if set — strip matching patterns from filename
    rename_regex = s.get("rename_regex", "") or ""
    if rename_regex:
        try:
            stem_r, ext_r = os.path.splitext(os.path.basename(file_path))
            stem_r = re.sub(rename_regex, " ", stem_r)
            stem_r = re.sub(r"\s+", " ", stem_r).strip()
            new_path = os.path.join(tmp_dir, stem_r + ext_r)
            if new_path != file_path:
                if os.path.exists(new_path): os.remove(new_path)
                os.rename(file_path, new_path)
                file_path = new_path
        except Exception:
            pass  # invalid regex — skip

    dump_channel = s.get("dump_channel", "") or ""
    caption_tpl  = s.get("caption", "") or ""

    # ── Build CLEAN filename (no tokens embedded) ──────────────
    orig_name  = os.path.basename(file_path)
    from bot.utils.rename import clean_name as _cn
    orig_stem  = os.path.splitext(_cn(orig_name))[0]  # clean stem for TMDB lookup
    final_name = await _build_filename(orig_name, prefix, suffix, file_path)
    renamed    = os.path.join(tmp_dir, final_name)
    if renamed != file_path:
        if os.path.exists(renamed): os.remove(renamed)
        os.rename(file_path, renamed)
        file_path = renamed

    file_size  = os.path.getsize(file_path)
    split_size = _split_size()

    if file_size > split_size:
        n_parts = math.ceil(file_size / split_size)
        limit   = "4 GB" if config.SESSION else "2 GB"
        try:
            await msg.edit_text(
                f"<b>✂️ SPLITTING FILE</b>\n\n"
                f"📄 <code>{final_name}</code>\n"
                f"📦 {human_size(file_size)} → {n_parts} parts of max {limit}\n\n"
                f"🆔 <code>{task_id}</code>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=task_kb(task_id),
            )
        except Exception:
            pass
        parts = await _split_file(file_path, split_size)
    else:
        parts = [file_path]

    if tm.is_cancelled(task_id):
        return

    thumb      = await _resolve_thumb(uid, orig_stem, tmp_dir)
    kb         = task_kb(task_id)
    task_start = time.monotonic()

    # Pre-build token info block (done once, reused for all parts)
    token_block = await _build_token_block(prefix, suffix, file_path)

    tm.set_status(task_id, "uploading")
    # Status card auto-refresh loop handles display — no direct edit needed

    last_sent_msg = None

    # When file was split, force document mode for all parts
    # This bypasses Telegram's 2 GB video/audio size limit enforcement
    is_split = len(parts) > 1

    for part in parts:
        if tm.is_cancelled(task_id): break
        if not os.path.isfile(part): continue

        part_name = os.path.basename(part)
        part_size = os.path.getsize(part)

        tm.update_progress(task_id, name=part_name, done=0,
                           total=part_size, speed=0.0, eta=0.0, status="uploading")

        _state = {"last_t": time.monotonic(), "last_b": 0}
        _SEP   = "━━━━━━━━━━━━━━━━━━━━━━━━"

        async def _cb(current, total,
                      _pn=part_name, _tid=task_id, _st=_state,
                      _pmsg=progress_msg):
            now = time.monotonic()
            dt  = now - _st["last_t"]
            if dt < config.PROGRESS_UPDATE_SEC: return
            speed = (current - _st["last_b"]) / dt if dt > 0 else 0.0
            eta   = (total - current) / speed if speed > 0 else 0.0
            _st["last_t"] = now; _st["last_b"] = current
            tm.update_progress(_tid, name=_pn, done=current,
                               total=total, speed=speed, eta=eta, status="uploading")
            # For encode flow: edit msg directly (no status loop running)
            if _pmsg:
                pct    = int(current * 100 / total) if total else 0
                filled = int(pct / 10)
                bar    = "█" * filled + "░" * (10 - filled)
                hspd   = f"{speed/1024/1024:.1f} MB/s" if speed >= 1024*1024 else f"{speed/1024:.1f} KB/s"
                hsize  = f"{current/1024/1024:.1f} MB" if current >= 1024*1024 else f"{current/1024:.1f} KB"
                htotal = f"{total/1024/1024:.1f} MB"   if total   >= 1024*1024 else f"{total/1024:.1f} KB"
                mm, ss = divmod(int(eta), 60)
                eta_s  = f"{mm}m {ss}s" if mm else f"{ss}s"
                stem   = (_pn[:36] + "…") if len(_pn) > 38 else _pn
                try:
                    await _pmsg.edit_text(
                        f"<b>{_SEP}</b>\n"
                        f"<b>📤  UPLOADING</b>\n"
                        f"<b>{_SEP}</b>\n\n"
                        f"🎬 <b>{stem}</b>\n\n"
                        f"<b><code>{bar}</code>  {pct}%</b>\n\n"
                        f"📦 <b>{hsize}</b> / <b>{htotal}</b>\n"
                        f"⚡ <b>{hspd}</b>\n"
                        f"🕐 <b>ETA: {eta_s}</b>\n\n"
                        f"<b>{_SEP}</b>\n"
                        f"<b>⚡ {config.WATERMARK}</b>",
                        parse_mode="html",
                    )
                except Exception:
                    pass

        # Caption = bold filename + token info block inside <blockquote>
        # The blockquote renders as Telegram's native "quote" style (grey bar on left)
        if token_block:
            caption = f"<b>{part_name}</b>\n\n<blockquote>{token_block}</blockquote>"
        else:
            caption = f"<b>{part_name}</b>"

        p_start = time.monotonic()

        for attempt in range(5):
            if tm.is_cancelled(task_id): raise asyncio.CancelledError
            try:
                last_sent_msg = await _send(client, chat_id, part, caption, thumb, _cb, as_doc or is_split)
                break
            except asyncio.CancelledError:
                raise
            except FloodWait as fw:
                await asyncio.sleep(fw.value * 1.3)
            except Exception:
                if attempt == 4: raise
                await asyncio.sleep(3 * (attempt + 1))

        elapsed = time.monotonic() - p_start
        avg_spd = part_size / max(elapsed, 0.001)

        if len(parts) == 1:
            uname_early = f"@{getattr(origin_msg.from_user, 'username', None) or uid}" \
                          if origin_msg else f"#{uid}"
            try:
                await msg.edit_text(
                    done_card(part_name, part_size, elapsed, avg_spd, task_id, uname_early),
                    parse_mode=enums.ParseMode.HTML, reply_markup=None,
                )
            except Exception:
                pass

        if thumb and os.path.exists(thumb) and "auto_thumb" in thumb:
            try: os.remove(thumb)
            except Exception: pass
            thumb = None

    if len(parts) > 1:
        for p in parts:
            try: os.remove(p)
            except Exception: pass

    # ── Dump channel — forward file to user's configured channel ──
    if dump_channel and last_sent_msg:
        try:
            dc = int(dump_channel)
            await last_sent_msg.copy(dc)
        except Exception:
            pass

    total_elapsed = time.monotonic() - task_start
    uname = f"@{getattr(origin_msg.from_user, 'username', None) or uid}" \
            if origin_msg else f"#{uid}"

    # Multi-part: show compact done_card in the status message
    if len(parts) > 1:
        avg_spd = file_size / max(total_elapsed, 0.001)
        try:
            await msg.edit_text(
                done_card(final_name, file_size, total_elapsed, avg_spd, task_id, uname),
                parse_mode=enums.ParseMode.HTML, reply_markup=None,
            )
        except Exception:
            pass

    # Group only: one compact reply so the requester sees it in the group
    if is_group and origin_msg:
        avg_spd = file_size / max(total_elapsed, 0.001)
        try:
            await origin_msg.reply_text(
                done_card(final_name, file_size, total_elapsed, avg_spd, task_id, uname),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
