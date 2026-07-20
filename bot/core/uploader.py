"""
Uploader — NXT_HUB v5

Key fixes:
  - Filename is CLEAN — no tokens ever embedded in it
  - Token info block sent as a SEPARATE quoted reply after the file
  - Telegram quoted reply used via reply_to_message_id + quote parameter
  - Watermark removed from file caption entirely
"""
from pyrogram import enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os, math, time, asyncio, aiofiles, re, shutil
import config

from pyrogram.errors import FloodWait, BadRequest, RPCError
from bot.core import task_manager as tm
from bot.utils.progress import done_card, task_kb
from bot.utils.size_utils import human_size
from bot.utils.thumbnail import get_thumbnail
from bot.utils.rename import smart_rename, parse_title_year
from bot.utils.file_links import get_bot_username
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

CHUNK          = 4 * 1024 * 1024           # 4 MB — fast read/write for large file splits
_2GB           = 2000 * 1024 * 1024       # 2000 MiB — Telegram's real practical limit
_4GB           = 4000 * 1024 * 1024       # 4000 MiB — Premium session limit
# NOTE: intentionally NOT 2*1024**3/4*1024**3 (2048/4096 MiB). Files in the
# 2000-2048 MiB gap were passing the old ">2GB" check as "fine, don't split"
# and then failing to upload outright since Telegram's actual enforced cap
# sits at the lower, safer number. This is the standard threshold used by
# most Telegram leech bots for exactly this reason.


def _split_size() -> int:
    """
    Return the correct split size based on whether a premium user session is
    configured AND usable. Without SESSION: 2 GB. With SESSION: 4 GB — but
    ONLY if DUMP_CHANNEL is also configured, since the premium user_app
    session can't message an arbitrary chat it has never interacted with
    (that's what was silently failing 2-4 GB uploads before: user_app
    tried to DM the requester directly and got rejected). With a relay
    channel available, uploads go through _send_relay() instead — user_app
    uploads there, then the bot copies it to the real destination, which
    always works since the bot is always a member of both.
    Files larger than this are automatically split into numbered parts.
    """
    if config.SESSION and getattr(config, "DUMP_CHANNEL", 0):
        return _4GB
    return _2GB

# Tokens that belong in the info block — never go into filename
_INFO_TOKENS = frozenset({"size", "language", "time", "quality", "codec", "audio", "fps", "ext", "date"})
_TOKEN_RE    = re.compile(r"\{(\w+)\}")


async def _split_file(path: str, part_size: int, progress_cb=None) -> list[str]:
    """
    Split file into numbered parts. Removes the original after all parts
    are written. If given, progress_cb(done, total, part_num, part_total,
    part_name) is called periodically (roughly every CHUNK read) so the
    caller can show a live progress bar instead of a static message.
    """
    total = os.path.getsize(path)
    if total == 0:
        raise ValueError(f"Cannot split empty file: {path}")
    n    = math.ceil(total / part_size)
    base, ext = os.path.splitext(path)
    parts: list[str] = []
    done = 0

    try:
        async with aiofiles.open(path, "rb") as src:
            for i in range(n):
                pp   = f"{base}.part{i+1:02d}{ext}"
                pp_name = os.path.basename(pp)
                left = part_size
                written = 0
                async with aiofiles.open(pp, "wb") as dst:
                    while left > 0:
                        chunk = await src.read(min(CHUNK, left))
                        if not chunk:
                            break
                        await dst.write(chunk)
                        left    -= len(chunk)
                        written += len(chunk)
                        done    += len(chunk)
                        if progress_cb:
                            try:
                                await progress_cb(done, total, i + 1, n, pp_name)
                            except Exception:
                                pass
                if written == 0:
                    # Empty tail part — remove and stop
                    try: os.remove(pp)
                    except Exception: pass
                    break
                parts.append(pp)
    except Exception:
        # Splitting failed partway — clean up whatever parts were already
        # written so a retry doesn't have to work around orphaned files
        for pp in parts:
            try: os.remove(pp)
            except Exception: pass
        raise

    # Remove the original so disk isn't holding both the source and all parts
    try:
        os.remove(path)
    except Exception:
        pass

    return parts


async def _resolve_thumb(uid, name, tmp_dir, title: str = ""):
    """
    Resolve thumbnail for upload.

    Priority:
      1. User-set custom thumb (thumb_path in settings)
      2. Auto: Fanart real logo+backdrop → TMDB backdrop+logo → TMDB poster
               → OMDB IMDb poster → iTunes poster → ffmpeg frame

    Returns None if no real poster can be found — no fake title card is generated.
    Every path goes through _prep_thumb() before send_video/send_document.
    """
    try:
        s = users_db.get_settings(uid)

        if s.get("thumb_path") and os.path.exists(s["thumb_path"]):
            src     = s["thumb_path"]
            resized = os.path.join(tmp_dir, f"thumb_hq_{uid}.jpg")
            return _hq_resize_thumb(src, resized, max_w=1280, max_h=720)

        from bot.utils.rename import parse_title_year
        t, year = parse_title_year(name)
        lookup_title = t or title or name
        if not lookup_title:
            return None

        dest = os.path.join(tmp_dir, f"auto_thumb_{uid}.jpg")
        from bot.utils.thumbnail import get_thumbnail

        # get_thumbnail tries Fanart → TMDB → OMDB → iTunes → ffmpeg frame
        # Returns False if no real poster found (no fake title card generated)
        found = await get_thumbnail(lookup_title, year, dest, title_overlay=lookup_title)

        if found and os.path.isfile(dest):
            return _hq_resize_thumb(dest, os.path.join(tmp_dir, f"auto_thumb_hq_{uid}.jpg"),
                                    max_w=1280, max_h=720)

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
    # Fallback: ffprobe (non-blocking)
    return await _ffprobe_dims(thumb_path)


def _prep_thumb(thumb_path: str) -> str | None:
    """320×320 JPEG for thumb= (Telegram hard limit)."""
    from bot.utils.thumb_store import prep_for_upload
    return prep_for_upload(thumb_path)


def _prep_cover(thumb_path: str) -> str | None:
    """1280×720 JPEG for cover= (PyroTGFork HD video cover)."""
    from bot.utils.thumb_store import prep_cover
    return prep_cover(thumb_path)


# ── Async wrappers — run the CPU-heavy PIL work (resize + JPEG re-encode,
# sometimes on a 4K source) in a background thread instead of blocking the
# asyncio event loop. A blocked event loop means EVERY other in-flight
# upload/download's progress callbacks stall too, not just this one —
# this was a real, silent tax on overall upload throughput whenever more
# than one task was running.
async def _prep_thumb_async(thumb_path: str) -> str | None:
    return await asyncio.get_event_loop().run_in_executor(None, _prep_thumb, thumb_path)


async def _prep_cover_async(thumb_path: str) -> str | None:
    return await asyncio.get_event_loop().run_in_executor(None, _prep_cover, thumb_path)


async def _ffprobe_dims(path: str) -> tuple[int, int]:
    """Non-blocking ffprobe — real video dimensions for Telegram player sizing."""
    from asyncio import create_subprocess_exec
    from asyncio.subprocess import PIPE
    try:
        pr = await create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", path,
            stdout=PIPE, stderr=PIPE,
        )
        out, _ = await asyncio.wait_for(pr.communicate(), timeout=5)
        import json as _j
        s = _j.loads(out.decode())["streams"][0]
        return int(s["width"]), int(s["height"])
    except Exception:
        return 1280, 720


# ── Send helpers ──────────────────────────────────────────────

async def _send_doc(client, chat_id, path, caption, thumb, cb):
    small = await _prep_thumb_async(thumb)
    tw, th = await _get_thumb_dims(small) if small else (None, None)
    return await client.send_document(
        chat_id=chat_id, document=path, thumb=small,
        file_name=os.path.basename(path),
        caption=caption, parse_mode=enums.ParseMode.HTML,
        disable_notification=True, progress=cb,
    )


# ── Relay wrapper for the premium (user_app) client ─────────────
# user_app is a separate Telegram account/session — it can only message
# chats it has actually interacted with. It has no relationship with
# whatever chat the requester messaged the BOT from, so sending directly
# to that chat_id via user_app fails. Route it through DUMP_CHANNEL
# instead (user_app is already a member there for the dump feature):
# upload the real bytes to DUMP_CHANNEL via user_app, then have the bot
# (always a legitimate member of the destination chat) copy it over —
# copy_message is a lightweight server-side op, no re-upload involved.
async def _send_relay(client, chat_id, path, caption, thumb, cb, as_doc, uid=0):
    from bot import user_app, app as bot_app

    if client is not user_app or not getattr(config, "DUMP_CHANNEL", 0):
        return await _send(client, chat_id, path, caption, thumb, cb, as_doc, uid)

    relay_chat = int(config.DUMP_CHANNEL)
    relayed = await _send(client, relay_chat, path, caption, thumb, cb, as_doc, uid)
    try:
        return await bot_app.copy_message(chat_id, relay_chat, relayed.id)
    except Exception:
        # Bot isn't in the relay channel, or copy failed — best-effort
        # cleanup of the relay copy, then surface the real error
        raise


async def _send(client, chat_id, path, caption, thumb, cb, as_doc, uid=0):
    """
    Upload file with dual-thumbnail support:
      thumb=  → 320×320 JPEG   (Telegram file list preview, hard limit)
      cover=  → 1280×720 JPEG  (HD video cover via PyroTGFork cover= param)

    Both are generated from the same source image.
    If PyroTGFork doesn't support cover= we fall back to thumb= only.
    """
    from bot.utils.hd_thumb import generate_hd_thumb

    is_video, is_audio, is_image = await get_document_type(path)

    # Auto-generate thumb if not available
    if not thumb and (is_video or is_audio):
        thumb = await generate_hd_thumb(path, uid=uid)

    # ── Documents ─────────────────────────────────────────────
    if as_doc or (not is_video and not is_audio and not is_image):
        small = await _prep_thumb_async(thumb)
        return await _send_doc(client, chat_id, path, caption, small, cb)

    # ── Video ─────────────────────────────────────────────────
    if is_video:
        duration, _, _ = await get_media_info(path)
        small = await _prep_thumb_async(thumb)   # 320×320 for thumb= (file list preview)
        cover = await _prep_cover_async(thumb)   # 1280×720 FULL QUALITY for cover= (video player)

        # Real video dimensions for Telegram player sizing
        vw, vh = await _ffprobe_dims(path)

        try:
            # Try with cover= first (PyroTGFork ≥ layer 166)
            # cover= sends the poster as a full Photo at full resolution
            # (NOT compressed to 200 KB — Telegram stores it at original quality)
            send_kwargs = dict(
                chat_id=chat_id, video=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, duration=duration or 0,
                width=vw, height=vh,
                thumb=small, file_name=os.path.basename(path),
                supports_streaming=True,
                disable_notification=True, progress=cb,
            )
            if cover:
                send_kwargs["cover"] = cover
            return await client.send_video(**send_kwargs)
        except TypeError:
            # cover= not supported in this pyrofork version — retry without it
            send_kwargs.pop("cover", None)
            try:
                return await client.send_video(**send_kwargs)
            except (BadRequest, RPCError):
                return await _send_doc(client, chat_id, path, caption, small, cb)
        except (BadRequest, RPCError):
            return await _send_doc(client, chat_id, path, caption, small, cb)

    # ── Audio ─────────────────────────────────────────────────
    if is_audio:
        duration, artist, title = await get_media_info(path)
        small = await _prep_thumb_async(thumb)
        try:
            return await client.send_audio(
                chat_id=chat_id, audio=path, caption=caption,
                parse_mode=enums.ParseMode.HTML, duration=duration or 0,
                performer=artist or "",
                title=title or os.path.splitext(os.path.basename(path))[0],
                thumb=small, file_name=os.path.basename(path),
                disable_notification=True, progress=cb,
            )
        except (BadRequest, RPCError):
            return await _send_doc(client, chat_id, path, caption, small, cb)

    # ── Image ─────────────────────────────────────────────────
    if is_image:
        try:
            return await client.send_photo(
                chat_id=chat_id, photo=path, caption=caption,
                parse_mode=enums.ParseMode.HTML,
                disable_notification=True, progress=cb,
            )
        except (BadRequest, RPCError):
            return await _send_doc(client, chat_id, path, caption, None, cb)

    return await _send_doc(client, chat_id, path, caption, None, cb)


# ── Main upload function ──────────────────────────────────────
async def upload_file(client, chat_id: int, file_path: str,
                      task_id: str, msg, uid: int,
                      origin_msg=None, is_group: bool = False,
                      progress_msg=None,
                      suppress_done_card: bool = False) -> None:
    """
    progress_msg: if provided (encode flow), upload progress is edited
                  directly on this message rather than via status loop.
    suppress_done_card: set True when uploading multiple files in a batch
                  (e.g. extracted zip) so done_card isn't sent after each
                  individual file. Caller sends one summary card at the end.
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

    # Per-user dump_channel from DB; fall back to owner's global DUMP_CHANNEL from config
    dump_channel = (
        s.get("dump_channel", "") or
        str(getattr(config, "DUMP_CHANNEL", 0) or "") or ""
    )
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
        limit   = "4 GB" if split_size == _4GB else "2 GB"
        tm.set_status(task_id, "splitting")

        # Splitting needs roughly another file_size worth of free disk
        # space (parts are written before the original is removed) —
        # check up front so a silent disk-full mid-split doesn't look
        # like a random "cancelled" task
        try:
            free = shutil.disk_usage(os.path.dirname(file_path) or ".").free
            if free < file_size * 1.05:
                raise RuntimeError(
                    f"Not enough disk space to split this file — need "
                    f"~{human_size(file_size)} free, have {human_size(free)}."
                )
        except RuntimeError:
            raise
        except Exception:
            pass  # disk_usage failed for some platform reason — proceed anyway

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

        _split_state = {"last_t": time.monotonic(), "last_b": 0, "started": time.monotonic()}
        _split_upd_sec = getattr(config, "PROGRESS_UPDATE_SEC", 4)

        async def _split_cb(done, total, part_num, part_total, part_name):
            from bot.utils.progress import build_progress_card, safe_edit
            now = time.monotonic()
            dt  = now - _split_state["last_t"]
            if dt < _split_upd_sec and done < total:
                return
            speed   = (done - _split_state["last_b"]) / dt if dt > 0 else 0.0
            eta     = (total - done) / speed if speed > 0 else 0.0
            elapsed = now - _split_state["started"]
            _split_state["last_t"] = now
            _split_state["last_b"] = done
            tm.update_progress(task_id, name=part_name, done=done,
                               total=total, speed=speed, eta=eta, status="splitting",
                               parent_name=final_name, part_num=part_num, part_total=part_total)
            pct = (done / total * 100) if total else 0
            await safe_edit(
                msg,
                build_progress_card(
                    "splitting", part_name, pct,
                    done=done, total=total, speed=speed, eta=eta,
                    elapsed=elapsed, tid=task_id,
                    parent_name=final_name, part_num=part_num, part_total=part_total,
                    user_mention=tm.get_user_mention(task_id),
                ),
                task_kb(task_id),
            )

        try:
            parts = await _split_file(file_path, split_size, progress_cb=_split_cb)
        except Exception as e:
            raise RuntimeError(f"Failed to split {final_name}: {e}") from e
    else:
        parts = [file_path]

    if tm.is_cancelled(task_id):
        return

    # HD thumbnail — 4-tier: custom → TMDB → Fanart → ffmpeg frame → title card
    from bot.utils.hd_thumb import generate_hd_thumb
    thumb = await generate_hd_thumb(file_path, uid=uid)
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
        _part_num = parts.index(part) + 1 if is_split else 0
        _part_total = len(parts) if is_split else 0

        tm.update_progress(task_id, name=part_name, done=0,
                           total=part_size, speed=0.0, eta=0.0, status="uploading",
                           parent_name=final_name if is_split else "",
                           part_num=_part_num, part_total=_part_total)

        _state   = {"last_t": time.monotonic(), "last_b": 0, "started": time.monotonic()}
        _upd_sec = getattr(config, "PROGRESS_UPDATE_SEC", 4)

        async def _cb(current, total,
                      _pn=part_name, _tid=task_id, _st=_state,
                      _pmsg=progress_msg, _pnum=_part_num, _ptot=_part_total):
            from bot.utils.progress import build_progress_card, safe_edit
            now     = time.monotonic()
            dt      = now - _st["last_t"]
            if dt < _upd_sec:
                return
            speed   = (current - _st["last_b"]) / dt if dt > 0 else 0.0
            eta     = (total - current) / speed if speed > 0 else 0.0
            elapsed = now - _st["started"]
            _st["last_t"] = now
            _st["last_b"] = current
            tm.update_progress(_tid, name=_pn, done=current,
                               total=total, speed=speed, eta=eta, status="uploading",
                               parent_name=final_name if is_split else "",
                               part_num=_pnum, part_total=_ptot)
            if _pmsg:
                pct = (current / total * 100) if total else 0
                await safe_edit(
                    _pmsg,
                    build_progress_card(
                        "uploading", _pn, pct,
                        done=current, total=total,
                        speed=speed, eta=eta, elapsed=elapsed,
                        tid=_tid,
                        parent_name=final_name if is_split else "",
                        part_num=_pnum, part_total=_ptot,
                        user_mention=tm.get_user_mention(_tid),
                    ),
                )

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
                last_sent_msg = await _send_relay(client, chat_id, part, caption, thumb, _cb, as_doc or is_split, uid)
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

    # ── Cleanup thumbnail after all parts uploaded ─────────────
    # Delete any auto-generated/prepped thumb. Never delete user's
    # permanent custom thumb which lives in data/thumbs/<uid>.jpg.
    if thumb and os.path.exists(thumb):
        from bot.utils.thumb_store import THUMB_DIR as _PERM_DIR
        is_permanent = os.path.dirname(os.path.abspath(thumb)) == os.path.abspath(_PERM_DIR)
        if not is_permanent:
            try: os.remove(thumb)
            except Exception: pass
            # Also remove any _thumb320 / _cover1280 derivatives
            for suffix in ("_thumb320.jpg", "_cover1280.jpg"):
                deriv = thumb.rsplit(".", 1)[0] + suffix
                if os.path.exists(deriv):
                    try: os.remove(deriv)
                    except Exception: pass
    thumb = None

    # Parts were already removed by _split_file after uploading each one
    # (original was removed by _split_file itself; no extra cleanup needed here)

    # ── Dump channel — forward file to owner's/user's dump channel ──
    if dump_channel and last_sent_msg:
        try:
            from bot.core.dump_channel import send_to_dump
            _dump_user = getattr(origin_msg, "from_user", None) if origin_msg else None
            await send_to_dump(client, last_sent_msg, _dump_user, final_name)
        except Exception:
            # Fallback: simple copy
            try:
                dc = int(dump_channel)
                await last_sent_msg.copy(dc)
            except Exception:
                pass

    total_elapsed = time.monotonic() - task_start
    uname = f"@{getattr(origin_msg.from_user, 'username', None) or uid}" \
            if origin_msg else f"#{uid}"

    # Multi-part: show compact done_card in the status message
    if len(parts) > 1 and not suppress_done_card:
        avg_spd = file_size / max(total_elapsed, 0.001)
        try:
            await msg.edit_text(
                done_card(final_name, file_size, total_elapsed, avg_spd, task_id, uname),
                parse_mode=enums.ParseMode.HTML, reply_markup=None,
            )
        except Exception:
            pass

    # Group only: one compact reply so the requester sees it in the group
    if is_group and origin_msg and not suppress_done_card:
        avg_spd = file_size / max(total_elapsed, 0.001)
        pm_kb = None
        try:
            bot_username = await get_bot_username(client)
            pm_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📥 View File in PM", url=f"https://t.me/{bot_username}")
            ]])
        except Exception:
            pm_kb = None
        try:
            await origin_msg.reply_text(
                done_card(final_name, file_size, total_elapsed, avg_spd, task_id, uname),
                parse_mode=enums.ParseMode.HTML,
                reply_markup=pm_kb,
            )
        except Exception:
            pass
