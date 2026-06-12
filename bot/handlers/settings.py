"""
Settings handler — NXT_HUB v5 with separated sections:
  📥 Download  |  📤 Upload  |  🎬 Encoding  |  🏷 Rename  |  🔒 Auth
"""
import os
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from bot.database import users_db
from bot.handlers._auth import auth_required
import config

_waiting: dict[int, str]       = {}
_wait_tasks: dict[int, asyncio.Task] = {}

THUMB_DIR   = "data/thumbs"
COOKIES_DIR = "data/cookies"


# ══════════════════════════════════════════════
#  ROOT SETTINGS MENU
# ══════════════════════════════════════════════

def _main_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Download",  callback_data="stg_sec:download"),
            InlineKeyboardButton("📤 Upload",    callback_data="stg_sec:upload"),
        ],
        [
            InlineKeyboardButton("🎬 Encoding",  callback_data="stg_sec:encoding"),
            InlineKeyboardButton("🏷 Rename",    callback_data="stg_sec:rename"),
        ],
        [
            InlineKeyboardButton("🔄 Reset All", callback_data="stg_reset"),
            InlineKeyboardButton("❌ Close",     callback_data="stg_close"),
        ],
    ])

def _main_text(uid: int) -> str:
    s = users_db.get_settings(uid)
    return (
        "<b>⚙️ Settings</b>\n\n"
        "Choose a section to configure:\n\n"
        "📥 <b>Download</b> — cookies for yt-dlp\n"
        "📤 <b>Upload</b> — thumbnail, mode, dump channel\n"
        "🎬 <b>Encoding</b> — FFmpeg encode settings\n"
        "🏷 <b>Rename</b> — prefix, suffix, regex, caption\n"
    )


# ══════════════════════════════════════════════
#  SECTION: DOWNLOAD
# ══════════════════════════════════════════════

def _download_kb(uid: int) -> InlineKeyboardMarkup:
    s = users_db.get_settings(uid)
    has_cookies = "✅" if s.get("cookies_path") and os.path.exists(s["cookies_path"]) else "❌"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🍪 Cookies {has_cookies}", callback_data="stg_set_cookies"),
            InlineKeyboardButton("🗑 Remove Cookies",         callback_data="stg_del_cookies"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _download_text(uid: int) -> str:
    s = users_db.get_settings(uid)
    ck = "Set ✅" if s.get("cookies_path") and os.path.exists(s["cookies_path"]) else "Not set ❌"
    return (
        "<b>📥 Download Settings</b>\n\n"
        f"🍪 <b>Cookies (yt-dlp):</b> {ck}\n\n"
        "Cookies allow yt-dlp to access age-restricted or premium content.\n"
        "Export from browser using <i>Get cookies.txt LOCALLY</i> extension."
    )


# ══════════════════════════════════════════════
#  SECTION: UPLOAD
# ══════════════════════════════════════════════

def _upload_kb(uid: int) -> InlineKeyboardMarkup:
    s        = users_db.get_settings(uid)
    mode     = "📄 Document" if s.get("as_doc") else "🎬 Media"
    has_thumb = "✅" if s.get("thumb_path") and os.path.exists(s["thumb_path"]) else "❌"
    dump_ch  = s.get("dump_channel") or "None"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🖼 Thumbnail {has_thumb}", callback_data="stg_set_thumb"),
            InlineKeyboardButton("🗑 Remove Thumb",           callback_data="stg_del_thumb"),
        ],
        [InlineKeyboardButton(f"📤 Upload as: {mode}",       callback_data="stg_toggle_mode")],
        [InlineKeyboardButton(f"📢 Dump Channel: {dump_ch[:20]}", callback_data="stg_dump")],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _upload_text(uid: int) -> str:
    s = users_db.get_settings(uid)
    th = "Set ✅" if s.get("thumb_path") and os.path.exists(s["thumb_path"]) else "Not set ❌"
    md = "Document" if s.get("as_doc") else "Media (Video/Audio)"
    dc = s.get("dump_channel") or "—"
    return (
        "<b>📤 Upload Settings</b>\n\n"
        f"🖼 <b>Custom Thumbnail:</b> {th}\n"
        f"📤 <b>Upload Mode:</b> {md}\n"
        f"📢 <b>Dump Channel:</b> <code>{dc}</code>\n"
    )


# ══════════════════════════════════════════════
#  SECTION: ENCODING
# ══════════════════════════════════════════════

def _encoding_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Open Encode Settings", callback_data="stg_enc_open")],
        [InlineKeyboardButton("👁 View Current Settings", callback_data="stg_enc_view")],
        [InlineKeyboardButton("🔄 Reset Encode Settings", callback_data="stg_enc_reset")],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _encoding_text() -> str:
    return (
        "<b>🎬 Encoding Settings</b>\n\n"
        "Configure FFmpeg encoding parameters:\n\n"
        "• Codec (H.264 / H.265)\n"
        "• CRF quality value\n"
        "• Resolution, preset, FPS\n"
        "• Audio codec, bitrate, channels\n"
        "• Subtitles (hardsub / softsub)\n"
        "• Watermark overlay\n\n"
        "Use /vset to see current settings as text."
    )


# ══════════════════════════════════════════════
#  SECTION: RENAME
# ══════════════════════════════════════════════

def _rename_kb(uid: int) -> InlineKeyboardMarkup:
    s = users_db.get_settings(uid)
    px = s.get("prefix") or "None"
    sx = s.get("suffix") or "None"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✏️ Prefix: {px[:20]}", callback_data="stg_prefix")],
        [InlineKeyboardButton(f"✏️ Suffix: {sx[:20]}", callback_data="stg_suffix")],
        [InlineKeyboardButton("🏷 Rename Regex",       callback_data="stg_rename_regex")],
        [InlineKeyboardButton("📝 Caption Template",   callback_data="stg_caption")],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _rename_text(uid: int) -> str:
    s = users_db.get_settings(uid)
    px  = s.get("prefix") or "—"
    sx  = s.get("suffix") or "—"
    rr  = s.get("rename_regex") or "—"
    cap = s.get("caption") or "—"
    return (
        "<b>🏷 Rename Settings</b>\n\n"
        f"✏️ <b>Prefix:</b> <code>{px[:40]}</code>\n"
        f"✏️ <b>Suffix:</b> <code>{sx[:40]}</code>\n"
        f"🏷 <b>Rename Regex:</b> <code>{rr[:40]}</code>\n"
        f"📝 <b>Caption:</b> <code>{cap[:40]}</code>\n\n"
        "Tokens: <code>{name}</code> <code>{size}</code> <code>{quality}</code>\n"
        "<code>{language}</code> <code>{codec}</code> <code>{audio}</code>"
    )


# ══════════════════════════════════════════════
#  /settings command
# ══════════════════════════════════════════════

@Client.on_message(filters.command("settings") & (filters.private | filters.group))
async def cmd_settings(client: Client, message: Message):
    if not await auth_required(message):
        return
    uid = message.from_user.id
    await message.reply_text(
        _main_text(uid),
        reply_markup=_main_settings_kb(),
        parse_mode=enums.ParseMode.HTML,
    )


# ══════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════

async def _safe_cb_edit(cb, text, kb=None):
    """Edit callback message, silently ignore MESSAGE_NOT_MODIFIED."""
    try:
        await cb.message.edit_text(
            text, reply_markup=kb, parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            raise


@Client.on_callback_query(filters.regex(r"^stg_"))
async def settings_cb(client: Client, cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data

    # ── Navigation ─────────────────────────────
    if data == "stg_main":
        await _safe_cb_edit(cb, _main_text(uid), _main_settings_kb())
        return await cb.answer()

    if data == "stg_sec:download":
        await _safe_cb_edit(cb, _download_text(uid), _download_kb(uid))
        return await cb.answer()

    if data == "stg_sec:upload":
        await _safe_cb_edit(cb, _upload_text(uid), _upload_kb(uid))
        return await cb.answer()

    if data == "stg_sec:encoding":
        await _safe_cb_edit(cb, _encoding_text(), _encoding_kb(uid))
        return await cb.answer()

    if data == "stg_sec:rename":
        await _safe_cb_edit(cb, _rename_text(uid), _rename_kb(uid))
        return await cb.answer()

    # ── Encoding section ────────────────────────
    if data == "stg_enc_open":
        try:
            from bot.encoding.settings_utils import OpenSettings
            from bot.encoding.db import enc_db
            await enc_db.add_user(uid)
            await OpenSettings(cb.message, user_id=uid)
        except Exception as e:
            await cb.answer(f"Error: {e}", show_alert=True)
        return

    if data == "stg_enc_view":
        try:
            from bot.encoding.db import enc_db
            codec  = "H.265" if await enc_db.get_hevc(uid) else "H.264"
            crf    = await enc_db.get_crf(uid)
            preset = await enc_db.get_preset(uid) or "sf"
            res    = await enc_db.get_resolution(uid) or "OG"
            audio  = await enc_db.get_audio(uid) or "aac"
            ext    = await enc_db.get_extensions(uid) or "MKV"
            hs     = "✅" if await enc_db.get_hardsub(uid) else "❌"
            ss     = "✅" if await enc_db.get_subtitles(uid) else "❌"
            wm     = "✅" if await enc_db.get_watermark(uid) else "❌"
            text = (
                f"<b>🎬 Current Encode Settings</b>\n\n"
                f"Codec: <code>{codec}</code>  CRF: <code>{crf}</code>\n"
                f"Preset: <code>{preset}</code>  Res: <code>{'Source' if res=='OG' else res+'p'}</code>\n"
                f"Audio: <code>{audio.upper()}</code>  Ext: <code>{ext}</code>\n"
                f"Hardsub: {hs}  Softsub: {ss}  Watermark: {wm}"
            )
            await cb.answer()
            await cb.message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await cb.answer(f"Error: {e}", show_alert=True)
        return

    if data == "stg_enc_reset":
        try:
            from bot.encoding.db import enc_db
            await enc_db.delete_user(uid)
            await enc_db.add_user(uid)
            await cb.answer("✅ Encoding settings reset.")
        except Exception as e:
            await cb.answer(f"Error: {e}", show_alert=True)
        return

    # ── Global reset / close ─────────────────────
    if data == "stg_reset":
        s = users_db.get_settings(uid)
        for key in ("thumb_path", "cookies_path"):
            p = s.get(key)
            if p and os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        users_db.reset_settings(uid)
        await cb.answer("✅ All leech settings reset.")
        await _safe_cb_edit(cb, _main_text(uid), _main_settings_kb())
        return

    if data == "stg_close":
        await cb.message.delete()
        return

    # ── Upload mode toggle ──────────────────────
    if data == "stg_toggle_mode":
        s = users_db.get_settings(uid)
        users_db.update_settings(uid, as_doc=not s.get("as_doc", False))
        await cb.answer("Upload mode toggled.")
        await _safe_cb_edit(cb, _upload_text(uid), _upload_kb(uid))
        return

    # ── Thumbnail ──────────────────────────────
    if data == "stg_set_thumb":
        _waiting[uid] = "thumb"
        _cancel_wait(uid)
        prompt = await cb.message.reply_text(
            "🖼 <b>Send your thumbnail</b>\n\n"
            "• Send as <b>Photo</b> — quick, Telegram compresses slightly\n"
            "• Send as <b>File</b> — preserves full original quality\n"
            "  (Android/iOS: 📎 → File | Desktop: Shift + drag)\n\n"
            "<i>Waiting 60s…</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        _wait_tasks[uid] = asyncio.ensure_future(_auto_cancel(uid, prompt, "thumb", 60))
        return await cb.answer()

    if data == "stg_del_thumb":
        s = users_db.get_settings(uid)
        p = s.get("thumb_path")
        if p and os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
        users_db.update_settings(uid, thumb_path=None)
        await cb.answer("🗑 Thumbnail removed.")
        await _safe_cb_edit(cb, _upload_text(uid), _upload_kb(uid))
        return

    # ── Cookies ────────────────────────────────
    if data == "stg_set_cookies":
        _waiting[uid] = "cookies"
        _cancel_wait(uid)
        prompt = await cb.message.reply_text(
            "🍪 <b>Send your <code>cookies.txt</code></b> (Netscape format) within 60s.\n\n"
            "Get it: <i>Get cookies.txt LOCALLY</i> browser extension.",
            parse_mode=enums.ParseMode.HTML,
        )
        _wait_tasks[uid] = asyncio.ensure_future(_auto_cancel(uid, prompt, "cookies", 60))
        return await cb.answer()

    if data == "stg_del_cookies":
        s = users_db.get_settings(uid)
        p = s.get("cookies_path")
        if p and os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
        users_db.update_settings(uid, cookies_path=None)
        await cb.answer("🗑 Cookies removed.")
        await _safe_cb_edit(cb, _download_text(uid), _download_kb(uid))
        return

    # ── Prefix / Suffix / Regex / Caption / Dump ─
    _text_keys = {
        "stg_prefix":       ("prefix",       "✏️ Send your <b>Prefix</b>"),
        "stg_suffix":       ("suffix",       "✏️ Send your <b>Suffix</b>"),
        "stg_rename_regex": ("rename_regex", "🏷 Send <b>Rename Regex</b>"),
        "stg_caption":      ("caption",      "📝 Send <b>Caption Template</b>"),
        "stg_dump":         ("dump_channel", "📢 Send <b>Dump Channel ID</b> (e.g. <code>-100XXXXXXXX</code>)"),
    }
    if data in _text_keys:
        key, hint = _text_keys[data]
        _waiting[uid] = key
        _cancel_wait(uid)
        prompt = await cb.message.reply_text(
            f"{hint}\n\nSend <code>clear</code> to remove.\n<i>Waiting 60s…</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        _wait_tasks[uid] = asyncio.ensure_future(_auto_cancel(uid, prompt, key, 60))
        return await cb.answer()

    await cb.answer()


# ── Timeout helper ─────────────────────────────────────────────

async def _auto_cancel(uid: int, prompt_msg, key: str, secs: int):
    await asyncio.sleep(secs)
    if _waiting.get(uid) == key:
        _waiting.pop(uid, None)
        try:
            await prompt_msg.edit_text(
                f"⏰ Timed out. No {key} received. Use /settings to try again.",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass


def _cancel_wait(uid: int):
    t = _wait_tasks.pop(uid, None)
    if t and not t.done():
        t.cancel()


# ── Photo → save thumbnail ─────────────────────────────────────

@Client.on_message(filters.photo & (filters.private | filters.group))
async def save_thumbnail_photo(client: Client, message: Message):
    """
    Accept photo for thumbnail. Telegram compresses photos to ~1280px but
    that is still usable. We additionally process it through _hq_resize_thumb.
    If user wants truly original quality they can send as file, but we accept
    both — no longer force document-only.
    """
    if not message.from_user: return
    uid = message.from_user.id
    if _waiting.get(uid) != "thumb": return
    _waiting.pop(uid, None)
    _cancel_wait(uid)
    os.makedirs(THUMB_DIR, exist_ok=True)
    raw  = os.path.join(THUMB_DIR, f"{uid}_raw.jpg")
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    # Download the largest available photo size
    await client.download_media(message.photo.file_id, file_name=raw)
    from bot.utils.media_utils import _hq_resize_thumb
    final = _hq_resize_thumb(raw, path, max_w=1280, max_h=720)
    try:
        if raw != final and os.path.exists(raw): os.remove(raw)
    except Exception:
        pass
    users_db.update_settings(uid, thumb_path=final)
    await message.reply_text(
        "✅ <b>Thumbnail saved!</b>\n"
        "<i>Tip: send as a File for full original quality.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


# ── Document → thumbnail (image file) or cookies ──────────────

@Client.on_message(filters.document & (filters.private | filters.group))
async def save_document(client: Client, message: Message):
    if not message.from_user: return
    uid = message.from_user.id
    doc = message.document
    waiting_for = _waiting.get(uid)

    # ── Thumbnail (image sent as file for full quality) ───────
    if waiting_for == "thumb":
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
            await message.reply_text(
                "❌ Unsupported format. Send a <b>JPG, PNG, or WebP</b> file.",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        _waiting.pop(uid, None)
        _cancel_wait(uid)
        os.makedirs(THUMB_DIR, exist_ok=True)
        raw  = os.path.join(THUMB_DIR, f"{uid}_raw{ext}")
        path = os.path.join(THUMB_DIR, f"{uid}.jpg")
        await client.download_media(doc.file_id, file_name=raw)
        from bot.utils.media_utils import _hq_resize_thumb
        final = _hq_resize_thumb(raw, path, max_w=1280, max_h=720)
        try:
            if raw != final and os.path.exists(raw): os.remove(raw)
        except Exception:
            pass
        users_db.update_settings(uid, thumb_path=final)
        await message.reply_text(
            "✅ <b>Thumbnail saved!</b> (full quality, q=95)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    # ── Cookies (.txt) ─────────────────────────────────────────
    if waiting_for == "cookies":
        if not (doc.file_name or "").lower().endswith(".txt"):
            await message.reply_text(
                "❌ Please send a <code>.txt</code> cookies file.",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        _waiting.pop(uid, None)
        _cancel_wait(uid)
        os.makedirs(COOKIES_DIR, exist_ok=True)
        path = os.path.join(COOKIES_DIR, f"{uid}.txt")
        await client.download_media(doc.file_id, file_name=path)
        users_db.update_settings(uid, cookies_path=path)
        await message.reply_text(
            "✅ <b>Cookies saved!</b> yt-dlp will now use them.",
            parse_mode=enums.ParseMode.HTML,
        )


# ── Text reply → prefix/suffix/regex/caption/dump ─────────────

@Client.on_message(filters.text & ~filters.command([]) & (filters.private | filters.group), group=5)
async def settings_text_reply(client: Client, message: Message):
    if not message.from_user: return
    uid = message.from_user.id
    valid = ("prefix", "suffix", "rename_regex", "caption", "dump_channel")
    if uid not in _waiting or _waiting[uid] not in valid:
        return
    key = _waiting.pop(uid)
    _cancel_wait(uid)
    raw = message.text.strip()
    val = "" if raw.lower() == "clear" else raw
    users_db.update_settings(uid, **{key: val})
    display = f"<code>{val}</code>" if val else "cleared"
    await message.reply_text(
        f"✅ <b>{key.replace('_', ' ').title()}</b> set to {display}.",
        parse_mode=enums.ParseMode.HTML,
    )
