"""
Settings handler — NXT HUB v5
Sections: 📥 Download | 📤 Upload | 🎬 Encoding | 🏷 Rename
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

_waiting: dict[int, str]            = {}
_wait_tasks: dict[int, asyncio.Task] = {}

THUMB_DIR   = "data/thumbs"
COOKIES_DIR = "data/cookies"

# ── Small helpers ─────────────────────────────────────────────
def _tick(val) -> str:  return "✅" if val else "❌"
def _or(v, fallback="—"): return v if v else fallback


# ══════════════════════════════════════════════════════════════
#  ROOT SETTINGS MENU
# ══════════════════════════════════════════════════════════════

def _main_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Download",   callback_data="stg_sec:download"),
            InlineKeyboardButton("📤 Upload",     callback_data="stg_sec:upload"),
        ],
        [
            InlineKeyboardButton("🎬 Encoding",   callback_data="stg_sec:encoding"),
            InlineKeyboardButton("🏷 Rename",     callback_data="stg_sec:rename"),
        ],
        [
            InlineKeyboardButton("📋 Overview",   callback_data="stg_overview"),
            InlineKeyboardButton("🔄 Reset All",  callback_data="stg_reset_confirm"),
        ],
        [
            InlineKeyboardButton("🏠 Home",       callback_data="nav_start"),
            InlineKeyboardButton("✖️ Close",      callback_data="stg_close"),
        ],
    ])

def _main_text(uid: int) -> str:
    s  = users_db.get_settings(uid)
    ck = _tick(s.get("cookies_path") and os.path.exists(s.get("cookies_path", "")))
    th = _tick(s.get("thumb_path")   and os.path.exists(s.get("thumb_path", "")))
    md = "Document 📄" if s.get("as_doc") else "Media 🎬"
    dc = "Set ✅" if s.get("dump_channel") else "—"
    px = f"<code>{s['prefix'][:16]}</code>" if s.get("prefix") else "—"
    sx = f"<code>{s['suffix'][:16]}</code>" if s.get("suffix") else "—"

    return (
        "⚙️ <b>Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 Cookies: {ck}   "
        f"📤 Mode: {md}\n"
        f"🖼 Thumb: {th}   "
        f"📢 Dump: {dc}\n"
        f"🏷 Prefix: {px}   Suffix: {sx}\n\n"
        "Tap a section below to configure it."
    )


# ══════════════════════════════════════════════════════════════
#  OVERVIEW  (all settings at a glance)
# ══════════════════════════════════════════════════════════════

async def _overview_text(uid: int) -> str:
    s  = users_db.get_settings(uid)
    ck = _tick(s.get("cookies_path") and os.path.exists(s.get("cookies_path", "")))
    th = _tick(s.get("thumb_path")   and os.path.exists(s.get("thumb_path", "")))
    md = "Document" if s.get("as_doc") else "Media"
    dc = f"<code>{s['dump_channel']}</code>" if s.get("dump_channel") else "—"
    px  = f"<code>{s.get('prefix','')[:30]}</code>" if s.get("prefix") else "—"
    sx  = f"<code>{s.get('suffix','')[:30]}</code>" if s.get("suffix") else "—"
    rr  = f"<code>{s.get('rename_regex','')[:30]}</code>" if s.get("rename_regex") else "—"
    cap = f"<code>{s.get('caption','')[:30]}</code>" if s.get("caption") else "—"

    # Encoding
    enc_line = "—"
    try:
        from bot.encoding.db import enc_db
        codec  = "H.265" if await enc_db.get_hevc(uid) else "H.264"
        crf    = await enc_db.get_crf(uid)
        preset = await enc_db.get_preset(uid) or "sf"
        res    = await enc_db.get_resolution(uid) or "OG"
        res_str = "Source" if res == "OG" else f"{res}p"
        audio  = (await enc_db.get_audio(uid) or "aac").upper()
        hs     = _tick(await enc_db.get_hardsub(uid))
        wm     = _tick(await enc_db.get_watermark(uid))
        enc_line = f"{codec}  CRF {crf}  {preset}  {res_str}  {audio}  Hardsub {hs}  WM {wm}"
    except Exception:
        pass

    return (
        "📋 <b>Settings Overview</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 <b>Download</b>\n"
        f"   Cookies: {ck}\n\n"
        f"📤 <b>Upload</b>\n"
        f"   Mode: {md}  ·  Thumbnail: {th}\n"
        f"   Dump channel: {dc}\n\n"
        f"🎬 <b>Encoding</b>\n"
        f"   {enc_line}\n\n"
        f"🏷 <b>Rename</b>\n"
        f"   Prefix: {px}\n"
        f"   Suffix: {sx}\n"
        f"   Regex:  {rr}\n"
        f"   Caption: {cap}\n\n"
        f"<i>{config.WATERMARK}</i>"
    )

def _overview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Back", callback_data="stg_main"),
    ]])


# ══════════════════════════════════════════════════════════════
#  SECTION: DOWNLOAD
# ══════════════════════════════════════════════════════════════

def _download_kb(uid: int) -> InlineKeyboardMarkup:
    s   = users_db.get_settings(uid)
    has = _tick(s.get("cookies_path") and os.path.exists(s.get("cookies_path", "")))
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🍪 Set Cookies {has}", callback_data="stg_set_cookies"),
            InlineKeyboardButton("🗑 Remove",              callback_data="stg_del_cookies"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _download_text(uid: int) -> str:
    s  = users_db.get_settings(uid)
    ck = "Set ✅" if (s.get("cookies_path") and os.path.exists(s.get("cookies_path", ""))) else "Not set ❌"
    return (
        "📥 <b>Download Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🍪 <b>Cookies (yt-dlp):</b> {ck}\n\n"
        "Cookies let yt-dlp access age-restricted or premium content.\n"
        "<i>Export from browser via <b>Get cookies.txt LOCALLY</b> extension,\n"
        "then send the .txt file here.</i>"
    )


# ══════════════════════════════════════════════════════════════
#  SECTION: UPLOAD
# ══════════════════════════════════════════════════════════════

def _upload_kb(uid: int) -> InlineKeyboardMarkup:
    s        = users_db.get_settings(uid)
    is_doc   = s.get("as_doc", False)
    mode_lbl = "📄 Document  ←" if is_doc else "🎬 Media  ←"
    has_th   = _tick(s.get("thumb_path") and os.path.exists(s.get("thumb_path", "")))
    dump_ch  = s.get("dump_channel") or "—"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🖼 Thumbnail {has_th}", callback_data="stg_set_thumb"),
            InlineKeyboardButton("🗑 Remove Thumb",         callback_data="stg_del_thumb"),
        ],
        [
            InlineKeyboardButton(f"📤 Switch to {'Media 🎬' if is_doc else 'Document 📄'}", callback_data="stg_toggle_mode"),
        ],
        [
            InlineKeyboardButton(f"📢 Dump Channel: {dump_ch[:22]}", callback_data="stg_dump"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _upload_text(uid: int) -> str:
    s  = users_db.get_settings(uid)
    th = "Set ✅" if (s.get("thumb_path") and os.path.exists(s.get("thumb_path", ""))) else "Not set ❌"
    md = "Document 📄" if s.get("as_doc") else "Media 🎬 (Video/Audio)"
    dc = f"<code>{s['dump_channel']}</code>" if s.get("dump_channel") else "—"
    return (
        "📤 <b>Upload Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖼 <b>Thumbnail:</b> {th}\n"
        f"📤 <b>Upload mode:</b> {md}\n"
        f"📢 <b>Dump channel:</b> {dc}\n\n"
        "<i>Send photo or image file for thumbnail.\n"
        "File upload preserves full quality.</i>"
    )


# ══════════════════════════════════════════════════════════════
#  SECTION: ENCODING
# ══════════════════════════════════════════════════════════════

def _encoding_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚙️ Open Encode Panel",   callback_data="stg_enc_open"),
            InlineKeyboardButton("👁 View Settings",        callback_data="stg_enc_view"),
        ],
        [
            InlineKeyboardButton("🔄 Reset to Defaults",   callback_data="stg_enc_reset"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _encoding_text() -> str:
    return (
        "🎬 <b>Encoding Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Configure your FFmpeg encoding profile.\n\n"
        "  <b>Codec</b>     H.264 or H.265\n"
        "  <b>CRF</b>       Quality (lower = better)\n"
        "  <b>Preset</b>    Speed vs compression\n"
        "  <b>Resolution</b> Source or target height\n"
        "  <b>FPS</b>       Framerate cap\n"
        "  <b>Audio</b>     Codec + bitrate + channels\n"
        "  <b>Hardsub</b>   Burn subtitles in\n"
        "  <b>Watermark</b> Overlay image\n\n"
        "<i>Use /vset to view settings as plain text.</i>"
    )


# ══════════════════════════════════════════════════════════════
#  SECTION: RENAME
# ══════════════════════════════════════════════════════════════

def _rename_kb(uid: int) -> InlineKeyboardMarkup:
    s  = users_db.get_settings(uid)
    px = s.get("prefix") or "—"
    sx = s.get("suffix") or "—"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✏️ Prefix: {px[:18]}", callback_data="stg_prefix"),
            InlineKeyboardButton(f"✏️ Suffix: {sx[:18]}", callback_data="stg_suffix"),
        ],
        [
            InlineKeyboardButton("🔎 Rename Regex",        callback_data="stg_rename_regex"),
            InlineKeyboardButton("📝 Caption Template",    callback_data="stg_caption"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="stg_main")],
    ])

def _rename_text(uid: int) -> str:
    s   = users_db.get_settings(uid)
    px  = f"<code>{s['prefix'][:40]}</code>" if s.get("prefix") else "—"
    sx  = f"<code>{s['suffix'][:40]}</code>" if s.get("suffix") else "—"
    rr  = f"<code>{s['rename_regex'][:40]}</code>" if s.get("rename_regex") else "—"
    cap = f"<code>{s['caption'][:40]}</code>" if s.get("caption") else "—"
    return (
        "🏷 <b>Rename Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✏️ <b>Prefix:</b>  {px}\n"
        f"✏️ <b>Suffix:</b>  {sx}\n"
        f"🔎 <b>Regex:</b>   {rr}\n"
        f"📝 <b>Caption:</b> {cap}\n\n"
        "<b>Tokens:</b>  "
        "<code>{name}</code> <code>{size}</code> <code>{quality}</code> "
        "<code>{language}</code> <code>{codec}</code> <code>{audio}</code> "
        "<code>{fps}</code> <code>{date}</code>\n\n"
        "<i>Send <code>clear</code> to remove a value.</i>"
    )


# ══════════════════════════════════════════════════════════════
#  /settings command
# ══════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════
#  Callbacks
# ══════════════════════════════════════════════════════════════

async def _safe_edit(cb, text, kb=None):
    try:
        await cb.message.edit_text(
            text, reply_markup=kb,
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            raise


@Client.on_callback_query(filters.regex(r"^stg_"))
async def settings_cb(client: Client, cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data

    # ── Navigation ─────────────────────────────────────────────
    if data == "stg_main":
        await _safe_edit(cb, _main_text(uid), _main_settings_kb())
        return await cb.answer()

    if data == "stg_overview":
        await _safe_edit(cb, await _overview_text(uid), _overview_kb())
        return await cb.answer()

    if data == "stg_sec:download":
        await _safe_edit(cb, _download_text(uid), _download_kb(uid))
        return await cb.answer()

    if data == "stg_sec:upload":
        await _safe_edit(cb, _upload_text(uid), _upload_kb(uid))
        return await cb.answer()

    if data == "stg_sec:encoding":
        await _safe_edit(cb, _encoding_text(), _encoding_kb(uid))
        return await cb.answer()

    if data == "stg_sec:rename":
        await _safe_edit(cb, _rename_text(uid), _rename_kb(uid))
        return await cb.answer()

    # ── Encoding sub-actions ────────────────────────────────────
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
            res_str= "Source" if res == "OG" else f"{res}p"
            audio  = (await enc_db.get_audio(uid) or "aac").upper()
            ext    = await enc_db.get_extensions(uid) or "MKV"
            hs     = _tick(await enc_db.get_hardsub(uid))
            ss     = _tick(await enc_db.get_subtitles(uid))
            wm     = _tick(await enc_db.get_watermark(uid))
            text = (
                "🎬 <b>Current Encode Settings</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Codec:     <code>{codec}</code>\n"
                f"CRF:       <code>{crf}</code>\n"
                f"Preset:    <code>{preset}</code>\n"
                f"Res:       <code>{res_str}</code>\n"
                f"Audio:     <code>{audio}</code>\n"
                f"Container: <code>{ext}</code>\n\n"
                f"Hardsub:   {hs}   Softsub: {ss}   Watermark: {wm}"
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
            await cb.answer("✅ Encoding settings reset to defaults.")
            await _safe_edit(cb, _encoding_text(), _encoding_kb(uid))
        except Exception as e:
            await cb.answer(f"Error: {e}", show_alert=True)
        return

    # ── Reset all — confirmation step ───────────────────────────
    if data == "stg_reset_confirm":
        await _safe_edit(
            cb,
            "⚠️ <b>Reset All Settings?</b>\n\n"
            "This will clear your thumbnail, cookies, prefix, suffix, regex and caption.\n"
            "<i>Encoding settings are reset separately.</i>",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Yes, reset",  callback_data="stg_reset"),
                    InlineKeyboardButton("❌ Cancel",       callback_data="stg_main"),
                ]
            ]),
        )
        return await cb.answer()

    if data == "stg_reset":
        s = users_db.get_settings(uid)
        for key in ("thumb_path", "cookies_path"):
            p = s.get(key)
            if p and os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
        users_db.reset_settings(uid)
        await cb.answer("✅ Settings reset.")
        await _safe_edit(cb, _main_text(uid), _main_settings_kb())
        return

    if data == "stg_close":
        await cb.message.delete()
        return

    # ── Upload mode toggle ──────────────────────────────────────
    if data == "stg_toggle_mode":
        s = users_db.get_settings(uid)
        users_db.update_settings(uid, as_doc=not s.get("as_doc", False))
        await cb.answer("✅ Upload mode switched.")
        await _safe_edit(cb, _upload_text(uid), _upload_kb(uid))
        return

    # ── Thumbnail ───────────────────────────────────────────────
    if data == "stg_set_thumb":
        _waiting[uid] = "thumb"
        _cancel_wait(uid)
        prompt = await cb.message.reply_text(
            "🖼 <b>Send your thumbnail</b>\n\n"
            "• <b>Photo</b> — quick send (Telegram compresses slightly)\n"
            "• <b>File</b> — preserves full quality\n"
            "  Android/iOS: 📎 → File  ·  Desktop: Shift + drag\n\n"
            "<i>Waiting 60 s…</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        _wait_tasks[uid] = asyncio.ensure_future(_auto_cancel(uid, prompt, "thumb", 60))
        return await cb.answer()

    if data == "stg_del_thumb":
        from bot.utils.thumb_store import delete_user_thumb
        delete_user_thumb(uid)
        users_db.update_settings(uid, thumb_path=None)
        await cb.answer("🗑 Thumbnail removed.")
        await _safe_edit(cb, _upload_text(uid), _upload_kb(uid))
        return

    # ── Cookies ─────────────────────────────────────────────────
    if data == "stg_set_cookies":
        _waiting[uid] = "cookies"
        _cancel_wait(uid)
        prompt = await cb.message.reply_text(
            "🍪 <b>Send your <code>cookies.txt</code></b> (Netscape format)\n\n"
            "<i>Get it via the <b>Get cookies.txt LOCALLY</b> browser extension.\n"
            "Waiting 60 s…</i>",
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
        await _safe_edit(cb, _download_text(uid), _download_kb(uid))
        return

    # ── Text fields (prefix / suffix / regex / caption / dump) ──
    _text_prompts = {
        "stg_prefix":       ("prefix",       "✏️ Send your <b>Prefix</b> text"),
        "stg_suffix":       ("suffix",       "✏️ Send your <b>Suffix</b> text"),
        "stg_rename_regex": ("rename_regex", "🔎 Send your <b>Rename Regex</b>"),
        "stg_caption":      ("caption",      "📝 Send your <b>Caption Template</b>\n\nAvailable tokens: <code>{name}</code> <code>{size}</code> <code>{quality}</code> <code>{codec}</code> <code>{fps}</code> <code>{date}</code>"),
        "stg_dump":         ("dump_channel", "📢 Send the <b>Dump Channel ID</b>\n(e.g. <code>-100123456789</code>)"),
    }
    if data in _text_prompts:
        key, hint = _text_prompts[data]
        _waiting[uid] = key
        _cancel_wait(uid)
        prompt = await cb.message.reply_text(
            f"{hint}\n\n<i>Send <code>clear</code> to remove · Waiting 60 s…</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        _wait_tasks[uid] = asyncio.ensure_future(_auto_cancel(uid, prompt, key, 60))
        return await cb.answer()

    await cb.answer()


# ── Timeout ───────────────────────────────────────────────────
async def _auto_cancel(uid: int, prompt_msg, key: str, secs: int):
    await asyncio.sleep(secs)
    if _waiting.get(uid) == key:
        _waiting.pop(uid, None)
        try:
            await prompt_msg.edit_text(
                f"⏰ <i>Timed out — no {key} received. Use /settings to try again.</i>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

def _cancel_wait(uid: int):
    t = _wait_tasks.pop(uid, None)
    if t and not t.done():
        t.cancel()


# ── Photo → thumbnail ─────────────────────────────────────────
@Client.on_message(filters.photo & (filters.private | filters.group))
async def save_thumbnail_photo(client: Client, message: Message):
    if not message.from_user: return
    uid = message.from_user.id
    if _waiting.get(uid) != "thumb": return
    _waiting.pop(uid, None)
    _cancel_wait(uid)
    from bot.utils.thumb_store import save_user_thumb, TMP_DIR as _TMP
    os.makedirs(_TMP, exist_ok=True)
    raw = os.path.join(_TMP, f"{uid}_raw.jpg")
    await client.download_media(message.photo.file_id, file_name=raw)
    final = save_user_thumb(uid, raw)
    try: os.remove(raw)
    except Exception: pass
    if not final:
        return await message.reply_text("❌ Failed to save thumbnail.", parse_mode=enums.ParseMode.HTML)
    users_db.update_settings(uid, thumb_path=final)
    await message.reply_text(
        "✅ <b>Thumbnail saved!</b>\n<i>Tip: send as a File for full original quality.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


# ── Document → thumbnail or cookies ───────────────────────────
@Client.on_message(filters.document & (filters.private | filters.group))
async def save_document(client: Client, message: Message):
    if not message.from_user: return
    uid         = message.from_user.id
    doc         = message.document
    waiting_for = _waiting.get(uid)

    if waiting_for == "thumb":
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"):
            return await message.reply_text(
                "❌ Unsupported format. Send a <b>JPG, PNG, or WebP</b> file.",
                parse_mode=enums.ParseMode.HTML,
            )
        _waiting.pop(uid, None)
        _cancel_wait(uid)
        from bot.utils.thumb_store import save_user_thumb, TMP_DIR as _TMP
        os.makedirs(_TMP, exist_ok=True)
        raw = os.path.join(_TMP, f"{uid}_raw{ext}")
        await client.download_media(doc.file_id, file_name=raw)
        final = save_user_thumb(uid, raw)
        try: os.remove(raw)
        except Exception: pass
        if not final:
            return await message.reply_text("❌ Failed to save thumbnail.", parse_mode=enums.ParseMode.HTML)
        users_db.update_settings(uid, thumb_path=final)
        await message.reply_text(
            "✅ <b>Thumbnail saved!</b> (full quality, q=95)",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if waiting_for == "cookies":
        if not (doc.file_name or "").lower().endswith(".txt"):
            return await message.reply_text(
                "❌ Please send a <code>.txt</code> cookies file.",
                parse_mode=enums.ParseMode.HTML,
            )
        _waiting.pop(uid, None)
        _cancel_wait(uid)
        os.makedirs(COOKIES_DIR, exist_ok=True)
        path = os.path.join(COOKIES_DIR, f"{uid}.txt")
        await client.download_media(doc.file_id, file_name=path)
        users_db.update_settings(uid, cookies_path=path)
        await message.reply_text(
            "✅ <b>Cookies saved!</b> yt-dlp will use them for downloads.",
            parse_mode=enums.ParseMode.HTML,
        )


# ── Text reply → prefix / suffix / regex / caption / dump ─────
@Client.on_message(filters.text & ~filters.command([]) & (filters.private | filters.group), group=5)
async def settings_text_reply(client: Client, message: Message):
    if not message.from_user: return
    uid   = message.from_user.id
    valid = ("prefix", "suffix", "rename_regex", "caption", "dump_channel")
    if uid not in _waiting or _waiting[uid] not in valid:
        return
    key = _waiting.pop(uid)
    _cancel_wait(uid)
    raw = message.text.strip()
    val = "" if raw.lower() == "clear" else raw
    users_db.update_settings(uid, **{key: val})
    display = f"<code>{val}</code>" if val else "cleared"
    label   = key.replace("_", " ").title()
    await message.reply_text(
        f"✅ <b>{label}</b> → {display}",
        parse_mode=enums.ParseMode.HTML,
    )
