import os
import re
import time
import asyncio
import shutil
import traceback

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import config
from bot.core import task_manager as tm
from bot.core.downloader import (
    http_download, ytdlp_download, jd_download, qbt_download,
    telegram_download, generate_direct_link,
)
from bot.core.uploader import upload_file
from bot.core.extractor import extract, make_zip, SUPPORTED_EXTRACT
from bot.utils.direct_links import resolve
from bot.utils.progress import error_card, cancel_card, task_kb, group_task_card, group_task_kb, status_message
from bot.utils.size_utils import human_size, human_size_pair, human_speed, human_time
from bot.handlers._auth import auth_required
from bot.database import users_db

_pending:   dict[str, dict] = {}
_selection: dict[str, set]  = {}


def _mention(user) -> str:
    """@username if set, else a clickable name mention, else 'someone'."""
    if not user:
        return "someone"
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = getattr(user, "first_name", None) or "User"
    return f'<a href="tg://user?id={user.id}">{name}</a>'


# ── Command handlers ──────────────────────────────────────────

async def _ensure_started(client: Client, message: Message) -> bool:
    """
    In a group: check if user has ever /start-ed the bot in PM.
    If not, send them a button to start and return False.
    In PM: always True (they're already talking to the bot).
    """
    if message.chat.type != enums.ChatType.PRIVATE:
        uid = message.from_user.id
        if not users_db.has_started(uid):
            from bot.handlers.start import send_start_prompt
            await send_start_prompt(client, message)
            return True  # blocked
    return False  # allowed


# ── Command handlers ──────────────────────────────────────────

@Client.on_message(filters.command("jdleech") & (filters.private | filters.group))
async def cmd_jdleech(client: Client, message: Message):
    """
    /jdleech <url> — resolve multi-host links and download.
    Supports: mediafire, pixeldrain, buzzheavier, gofile, terabox,
    1fichier, krakenfiles, wetransfer, onedrive, yandex, streamtape, etc.
    """
    if not await auth_required(message):
        return
    if await _ensure_started(client, message):
        return

    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply_text(
            "❌ Usage: <code>/jdleech &lt;url&gt;</code>\n\n"
            "Supported hosts: mediafire, pixeldrain, buzzheavier, gofile, terabox,\n"
            "1fichier, krakenfiles, wetransfer, onedrive, yandex disk, streamtape,\n"
            "doodstream, filelions/streamwish, and more.",
            parse_mode=enums.ParseMode.HTML,
        )

    url = parts[1].strip()
    await _start_jdleech(client, message, url)


async def _start_jdleech(client: Client, message: Message, url: str):
    uid = message.from_user.id
    is_group = message.chat.type != enums.ChatType.PRIVATE

    can, reason = tm.can_add_task(uid)
    if not can:
        cap = (
            f"⚠️ Bot at capacity (<b>{config.TOTAL_TASKS}</b> tasks). Please wait."
            if reason == "global" else
            f"⚠️ You have <b>{config.MAX_TASKS}</b> active tasks. Use /status."
        )
        return await message.reply_text(cap, parse_mode=enums.ParseMode.HTML)

    tid      = tm.create_task(uid, url[:60], _mention(message.from_user))
    dest_dir = os.path.join(config.DOWNLOAD_DIR, tid)

    loop = asyncio.get_event_loop()
    coro_task = loop.create_task(_run_jdleech(client, message, url, tid, dest_dir, uid, is_group))
    tm.set_asyncio_task(tid, coro_task)


async def _run_jdleech(client, message, url, tid, dest_dir, uid, is_group):
    await _send_or_update_status_card(uid, message)
    msg = _status_msgs.get(uid)
    if not msg:
        msg = await message.reply_text(
            f"🔗 Resolving JDLeech…\n🆔 <code>{tid}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=task_kb(tid),
        )

    start = time.monotonic()
    try:
        os.makedirs(dest_dir, exist_ok=True)
        path = await jd_download(url, dest_dir, tid, msg, uid)
        paths = [path] if isinstance(path, str) and os.path.isfile(path) else \
                [os.path.join(path, f) for f in os.listdir(path)] if os.path.isdir(path) else []

        await _post_download(client, message, msg, paths or [path], dest_dir,
                             tid, uid, "", start, is_group)
    except asyncio.CancelledError:
        await _cancel_msg(msg, tid, uid=uid)
    except Exception as e:
        await _error_msg(msg, tid, e, uid=uid)
    finally:
        tm.finish_task(tid)
        _cleanup(dest_dir)
        await _refresh_status_card(uid)


@Client.on_message(filters.command("d") & (filters.private | filters.group))
async def cmd_download(client: Client, message: Message):
    if not await auth_required(message):
        return
    if await _ensure_started(client, message):
        return

    parts  = message.text.split(None, 2)
    action = ""
    if len(parts) == 3 and parts[2].strip().lower() in ("zip", "unzip"):
        action = parts[2].strip().lower()
    elif len(parts) == 2 and parts[1].strip().lower() in ("zip", "unzip"):
        action = parts[1].strip().lower()

    # ── Case 1: user replied to a Telegram media message with /d ──
    replied = message.reply_to_message
    if replied and (
        replied.video or replied.document or replied.audio or
        replied.photo or replied.animation or replied.voice or
        replied.video_note
    ):
        await _start_tg_reply(client, message, replied, action)
        return

    # ── Case 2: user replied to a text/link message with /d ──
    if replied and replied.text:
        url = replied.text.strip()
        if re.match(r"https?://\S+", url):
            await _start(client, message, url, action)
            return

    # ── Case 3: /d <url> [zip|unzip] ──
    if len(parts) < 2 or parts[1].strip().lower() in ("zip", "unzip"):
        return await message.reply_text(
            "❌ Usage:\n"
            "• <code>/d &lt;url&gt; [zip|unzip]</code> — download a link\n"
            "• Reply to any Telegram file with <code>/d</code> — re-upload it\n"
            "• Reply to any message containing a link with <code>/d</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = parts[1].strip()
    await _start(client, message, url, action)


# ── Auto-detect reply to media (no command needed) ───────────
# When user replies to any media message without typing anything,
# treat it as /d — download and re-upload that file.

@Client.on_message(
    filters.reply & filters.command(["leech", "l"]) &
    (filters.private | filters.group)
)
async def cmd_leech(client: Client, message: Message):
    """Alias: /l or /leech as reply to a media message."""
    if not await auth_required(message):
        return
    if await _ensure_started(client, message):
        return
    replied = message.reply_to_message
    if not replied:
        return
    if replied.video or replied.document or replied.audio or \
       replied.photo or replied.animation or replied.voice or replied.video_note:
        await _start_tg_reply(client, message, replied, "")
    elif replied.text and re.match(r"https?://\S+", replied.text.strip()):
        await _start(client, message, replied.text.strip(), "")


@Client.on_message(
    filters.reply &
    ~filters.command([]) &
    (filters.private | filters.group),
    group=2
)
async def reply_to_media(client: Client, message: Message):
    """
    Download a Telegram media file when user replies to it.
    Only triggers when the reply text is EMPTY or is exactly a
    known trigger word — never on normal conversation replies.
    This prevents the bot from responding to every message in a chat.
    """
    # Only trigger if:
    # 1. Reply text is empty (bare reply to media = download intent)
    # 2. Or reply text is exactly a trigger word
    TRIGGER_WORDS = {"dl", "download", "leech", "d", "get", "save"}
    text = (message.text or message.caption or "").strip().lower()

    if text:
        # Has text — only proceed if it's a recognised trigger word
        # Ignore: normal conversation, questions, URLs (handled by /d), commands
        if text not in TRIGGER_WORDS:
            return
        if text.startswith("/"):
            return
        if re.match(r"https?://\S+", text):
            return

    if not await auth_required(message):
        return
    if await _ensure_started(client, message):
        return

    replied = message.reply_to_message
    if not replied:
        return

    # Only trigger if replied message has downloadable media
    media = (replied.video or replied.document or replied.audio or
             replied.animation or replied.voice or replied.video_note)
    if not media:
        return

    await _start_tg_reply(client, message, replied, "")


# ── Core task flow ────────────────────────────────────────────

async def _start_tg_reply(client: Client, message: Message, replied, action: str):
    """Handle /d when user replies to a Telegram media message directly."""
    uid = message.from_user.id

    can, reason = tm.can_add_task(uid)
    if not can:
        cap = (
            f"⚠️ The bot is at full capacity (<b>{config.TOTAL_TASKS}</b> active tasks).\n"
            "Please wait for a slot to free up." if reason == "global" else
            f"⚠️ You already have <b>{config.MAX_TASKS}</b> active tasks.\n"
            "Use /status to check them."
        )
        return await message.reply_text(cap, parse_mode=enums.ParseMode.HTML)

    tid      = tm.create_task(uid, "tg_reply", _mention(message.from_user))
    dest_dir = os.path.join(config.DOWNLOAD_DIR, tid)

    loop      = asyncio.get_event_loop()
    coro_task = loop.create_task(
        _run_tg_reply(client, message, replied, action, tid, dest_dir, uid)
    )
    tm.set_asyncio_task(tid, coro_task)


async def _run_tg_reply(client: Client, message: Message, replied, action: str,
                        tid: str, dest_dir: str, uid: int):
    """Inner coroutine — runs as asyncio.Task so it can be hard-cancelled."""
    is_group = message.chat.type != enums.ChatType.PRIVATE
    os.makedirs(dest_dir, exist_ok=True)

    # Determine filename from the replied media
    media = (replied.video or replied.document or replied.audio or
             replied.photo or replied.animation or replied.voice or
             replied.video_note)
    fname = getattr(media, "file_name", None)
    if not fname:
        import mimetypes
        mime = getattr(media, "mime_type", "") or ""
        # mimetypes.guess_extension is unreliable — use manual map first
        _mime_map = {
            "video/mp4": ".mp4", "video/x-matroska": ".mkv",
            "video/x-msvideo": ".avi", "video/quicktime": ".mov",
            "video/x-ms-wmv": ".wmv", "video/webm": ".webm",
            "video/mpeg": ".mpeg", "video/3gpp": ".3gp",
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            "audio/ogg": ".ogg", "audio/flac": ".flac",
            "audio/x-wav": ".wav", "audio/aac": ".aac",
            "image/jpeg": ".jpg", "image/png": ".png",
            "image/gif": ".gif", "image/webp": ".webp",
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "application/x-rar-compressed": ".rar",
            "application/x-7z-compressed": ".7z",
        }
        base_mime = mime.split(";")[0].strip()
        ext = _mime_map.get(base_mime) or mimetypes.guess_extension(base_mime) or ""
        # Never use .bin — fallback by media type
        if not ext or ext == ".bin" or ext == ".ksh":
            if message.video or (message.document and "video" in mime):
                ext = ".mp4"
            elif message.audio:
                ext = ".mp3"
            elif message.photo:
                ext = ".jpg"
            else:
                ext = ".bin"
        fname = f"tg_{tid}{ext}"
    dest = os.path.join(dest_dir, fname)

    # Set up progress card
    if is_group:
        existing = _group_msgs.get(uid)
        if existing:
            msg = existing
            try:
                await msg.edit_text(
                    group_task_card(uid),
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=group_task_kb(uid),
                )
            except Exception:
                msg = await message.reply_text(
                    group_task_card(uid),
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=group_task_kb(uid),
                )
                _upsert_group_msg(uid, msg)
        else:
            msg = await message.reply_text(
                group_task_card(uid),
                parse_mode=enums.ParseMode.HTML,
                reply_markup=group_task_kb(uid),
            )
            _upsert_group_msg(uid, msg)
    else:
        msg = await message.reply_text(
            f"📥 Downloading Telegram file…\n🆔 <code>{tid}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=task_kb(tid),
        )

    start = time.monotonic()
    try:
        from pyrogram import enums as _enums
        from bot.utils.progress import downloading_card

        total = getattr(media, "file_size", 0)
        tm.update_progress(tid, name=fname, done=0, total=total,
                           speed=0.0, eta=0.0, status="downloading")
        _state = {"last_t": time.monotonic(), "last_b": 0}

        async def _prog(current, t):
            now = time.monotonic()
            dt  = now - _state["last_t"]
            if dt < config.PROGRESS_UPDATE_SEC:
                return
            speed = (current - _state["last_b"]) / dt if dt > 0 else 0.0
            eta   = (t - current) / speed if speed > 0 else 0.0
            _state["last_t"] = now
            _state["last_b"] = current
            tm.update_progress(tid, name=fname, done=current,
                               total=t, speed=speed, eta=eta, status="downloading")
            try:
                await msg.edit_text(
                    downloading_card(fname, current, t, speed, eta, tid),
                    reply_markup=task_kb(tid),
                    parse_mode=_enums.ParseMode.HTML,
                )
            except Exception:
                pass

        path = await client.download_media(replied, file_name=dest, progress=_prog)
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("Telegram download returned no file.")

        await _post_download(client, message, msg, [path], dest_dir,
                             tid, uid, action, start, is_group)

    except asyncio.CancelledError:
        await _cancel_msg(msg, tid)
    except Exception as e:
        print(f"[{tid}] TG-REPLY ERROR: {e}")
        await _error_msg(msg, tid, e)
    finally:
        tm.finish_task(tid)
        _cleanup(dest_dir)



async def _start(client: Client, message: Message, url: str, action: str):
    uid = message.from_user.id
    is_group = message.chat.type != enums.ChatType.PRIVATE

    can, reason = tm.can_add_task(uid)
    if not can:
        if reason == "global":
            _cap_text = (
                f"⚠️ The bot is at full capacity (<b>{config.TOTAL_TASKS}</b> active tasks).\n"
                "Please wait for a slot to free up."
            )
        else:
            _cap_text = (
                f"⚠️ You already have <b>{config.MAX_TASKS}</b> active tasks.\n"
                "Use /status to check them or /cancel &lt;id&gt; to free a slot."
            )
        return await message.reply_text(_cap_text, parse_mode=enums.ParseMode.HTML)

    tid      = tm.create_task(uid, url[:60], _mention(message.from_user))
    dest_dir = os.path.join(config.DOWNLOAD_DIR, tid)

    # Schedule as an asyncio.Task so cancel_task() can hard-cancel it
    loop = asyncio.get_event_loop()
    coro_task = loop.create_task(_run_start(client, message, url, action, tid, dest_dir, uid))
    tm.set_asyncio_task(tid, coro_task)
    return


# ── Global status card — one per user, auto-refreshes every 5s ──────────────
# One card shows ALL user tasks. Sent when first task added, updated on every
# task add/complete/cancel. Auto-refresh loop keeps it live every 5 seconds.

_status_msgs:   dict[int, Message]        = {}
_refresh_tasks: dict[int, asyncio.Task]   = {}
_group_msgs:    dict[int, Message]        = {}  # kept for compat


def _upsert_group_msg(uid: int, msg: Message):
    _group_msgs[uid] = msg


async def _send_or_update_status_card(uid: int, origin_msg: Message):
    """
    Called every time a NEW task is added. Deletes the old status card (if any)
    and posts a fresh one, so the status always reappears at the bottom of the
    chat next to the new task instead of staying stuck up where it first sent.
    """
    from bot.utils.progress import status_message
    from bot.handlers.status import _status_kb
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}
    text  = status_message(tasks)
    kb    = _status_kb(uid)

    existing = _status_msgs.pop(uid, None)
    if existing:
        try:
            await existing.delete()
        except Exception:
            pass

    try:
        msg = await origin_msg.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
        _status_msgs[uid] = msg
        _start_refresh_loop(uid)
    except Exception:
        pass


def _start_refresh_loop(uid: int):
    """Background 5s auto-refresh for the user's status card."""
    old = _refresh_tasks.get(uid)
    if old and not old.done():
        old.cancel()

    async def _loop():
        from bot.utils.progress import status_message
        from bot.handlers.status import _status_kb
        last_text = ""
        while True:
            await asyncio.sleep(3)
            msg = _status_msgs.get(uid)
            if not msg:
                break
            tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}
            if not tasks:
                _status_msgs.pop(uid, None)
                _refresh_tasks.pop(uid, None)
                break
            new_text = status_message(tasks)
            if new_text == last_text:
                continue
            last_text = new_text
            try:
                await msg.edit_text(
                    new_text,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=_status_kb(uid),
                )
            except Exception:
                pass

    _refresh_tasks[uid] = asyncio.ensure_future(_loop())


async def _refresh_status_card(uid: int):
    """Immediate refresh after task completes/cancels/errors."""
    from bot.utils.progress import status_message
    from bot.handlers.status import _status_kb
    msg = _status_msgs.get(uid)
    if not msg:
        return
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}
    try:
        await msg.edit_text(
            status_message(tasks),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=_status_kb(uid),
        )
    except Exception:
        pass
    if not tasks:
        _status_msgs.pop(uid, None)


@Client.on_message(filters.command("torrent") & (filters.private | filters.group))
async def cmd_torrent(client: Client, message: Message):
    """
    /torrent — always forces the torrent/magnet path, regardless of how
    the link is shaped (unlike /d, which has to guess from the URL).
    Works three ways:
      • /torrent <magnet_or_.torrent_url>
      • reply to a message containing a magnet/.torrent link
      • reply to an uploaded .torrent FILE
    """
    if not await auth_required(message):
        return
    if await _ensure_started(client, message):
        return

    replied = message.reply_to_message

    # Case 1: reply to an uploaded .torrent file
    if replied and replied.document:
        fname = (replied.document.file_name or "").lower()
        is_torrent_doc = (
            fname.endswith(".torrent") or
            replied.document.mime_type == "application/x-bittorrent"
        )
        if is_torrent_doc:
            await _start_torrent(client, message, replied=replied)
            return

    # Case 2: reply to a message containing a magnet/torrent link
    if replied and replied.text:
        txt = replied.text.strip()
        if txt.startswith("magnet:") or re.match(r"https?://\S+", txt):
            await _start_torrent(client, message, url=txt)
            return

    # Case 3: /torrent <magnet_or_url>
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply_text(
            "❌ Usage:\n"
            "• <code>/torrent &lt;magnet_or_.torrent_url&gt;</code>\n"
            "• Reply to a message with a magnet/.torrent link, then <code>/torrent</code>\n"
            "• Reply to an uploaded <code>.torrent</code> file with <code>/torrent</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = parts[1].strip()
    if not (url.startswith("magnet:") or re.match(r"https?://\S+", url)):
        return await message.reply_text(
            "❌ That doesn't look like a magnet link or a URL.",
            parse_mode=enums.ParseMode.HTML,
        )
    await _start_torrent(client, message, url=url)


async def _start_torrent(client: Client, message: Message, url: str | None = None, replied=None):
    uid = message.from_user.id

    can, reason = tm.can_add_task(uid)
    if not can:
        cap = (
            f"⚠️ The bot is at full capacity (<b>{config.TOTAL_TASKS}</b> active tasks).\n"
            "Please wait for a slot to free up." if reason == "global" else
            f"⚠️ You already have <b>{config.MAX_TASKS}</b> active tasks.\n"
            "Use /status to check them or /cancel &lt;id&gt; to free a slot."
        )
        return await message.reply_text(cap, parse_mode=enums.ParseMode.HTML)

    label    = (url or "torrent_file")[:60]
    tid      = tm.create_task(uid, label, _mention(message.from_user))
    dest_dir = os.path.join(config.DOWNLOAD_DIR, tid)

    loop = asyncio.get_event_loop()
    coro_task = loop.create_task(_run_torrent(client, message, url, replied, tid, dest_dir, uid))
    tm.set_asyncio_task(tid, coro_task)


async def _run_torrent(client: Client, message: Message, url: str | None, replied,
                        tid: str, dest_dir: str, uid: int):
    is_group = message.chat.type != enums.ChatType.PRIVATE
    action = ""

    await _send_or_update_status_card(uid, message)
    msg = _status_msgs.get(uid)
    if not msg:
        msg = await message.reply_text(
            f"🌊 Adding torrent...\n🆔 <code>{tid}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=task_kb(tid),
        )

    start = time.monotonic()
    try:
        os.makedirs(dest_dir, exist_ok=True)
        from bot.downloaders.aria2_downloader import torrent_download

        if replied is not None:
            await _edit(msg, tid, "📥 Downloading .torrent file...", uid, is_group)
            source = await client.download_media(
                replied, file_name=os.path.join(dest_dir, "source.torrent"),
            )
        else:
            source = url

        await _edit(msg, tid, "🌊 Adding torrent...", uid, is_group)
        path = await torrent_download(source, dest_dir, tid, msg, uid)
        await _post_download(client, message, msg, [path], dest_dir, tid, uid, action, start, is_group)

    except asyncio.CancelledError:
        tm.cancel_task(tid)
        task_name = tm.get_task(tid) or {}
        await _cancel_msg(msg, tid, uid=uid, task_name=task_name.get("name", ""))
    except Exception as e:
        print(f"[{tid}] ERROR:\n{traceback.format_exc()}")
        await _error_msg(msg, tid, e, uid=uid)
    finally:
        tm.finish_task(tid)
        _cleanup(dest_dir)
        await _refresh_status_card(uid)
        if not is_group:
            try:
                await msg.delete()
            except Exception:
                pass


async def _run_start(client: Client, message: Message, url: str, action: str,
                     tid: str, dest_dir: str, uid: int):
    is_group = message.chat.type != enums.ChatType.PRIVATE

    # Send/update global status card immediately when task is added
    await _send_or_update_status_card(uid, message)

    # Status card IS the progress card — use it as msg for edits
    msg = _status_msgs.get(uid)
    if not msg:
        # Fallback: send a simple reply
        msg = await message.reply_text(
            f"🔍 Resolving...\n🆔 <code>{tid}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=task_kb(tid),
        )

    start = time.monotonic()

    try:
        info = await resolve(url)
        os.makedirs(dest_dir, exist_ok=True)

        if info["is_magnet"] or info["is_torrent"]:
            from bot.downloaders.aria2_downloader import torrent_download
            path = await torrent_download(info["url"], dest_dir, tid, msg, uid)
            await _post_download(client, message, msg, [path], dest_dir, tid, uid, action, start, is_group)
            return

        if info.get("is_gdrive"):
            await _edit(msg, tid, "📂 Downloading from Google Drive…", uid, is_group)
            from bot.downloaders.gdrive_downloader import gdrive_download
            path = await gdrive_download(info["url"], dest_dir, tid, msg, uid)

        elif info.get("is_tg"):
            await _edit(msg, tid, "📥 Downloading from Telegram...", uid, is_group)
            from bot.utils.tg_downloader import download_tg_link
            path = await download_tg_link(info["url"], dest_dir, tid, msg)

        elif info.get("is_mega"):
            # Mega removed — route via yt-dlp fallback
            await _edit(msg, tid, "⬇️ Fetching via yt-dlp...", uid, is_group)
            path = await ytdlp_download(info["url"], dest_dir, tid, msg, uid)

        elif info.get("is_jdleech"):
            await _edit(msg, tid, "🔗 Resolving via JDLeech...", uid, is_group)
            path = await jd_download(info["url"], dest_dir, tid, msg, uid)

        elif info["use_ytdlp"]:
            await _edit(msg, tid, "⬇️ Fetching via yt-dlp...", uid, is_group)
            path = await ytdlp_download(info["url"], dest_dir, tid, msg, uid)
        else:
            await _edit(msg, tid, "⬇️ Downloading...", uid, is_group)
            path = await http_download(info["url"], dest_dir, tid, msg)

        await _post_download(client, message, msg, [path], dest_dir, tid, uid, action, start, is_group)

    except asyncio.CancelledError:
        # Only cancel THIS task — not siblings
        tm.cancel_task(tid)
        task_name = tm.get_task(tid) or {}
        await _cancel_msg(msg, tid, uid=uid,
                          task_name=task_name.get("name", ""))
    except Exception as e:
        print(f"[{tid}] ERROR:\n{traceback.format_exc()}")
        await _error_msg(msg, tid, e, uid=uid)
    finally:
        tm.finish_task(tid)
        _cleanup(dest_dir)
        # Refresh the global status card after task ends
        await _refresh_status_card(uid)
        # Only delete individual PM progress msg, not the group shared card
        if not (message.chat.type != enums.ChatType.PRIVATE):
            try:
                await msg.delete()
            except Exception:
                pass



async def _refresh_group_card(uid: int):
    """Kept for compat — delegates to _refresh_status_card."""
    await _refresh_status_card(uid)


# ── Post-download: upload to user PM (in groups) ─────────────

async def _post_download(client, message, msg, paths, dest_dir, tid, uid, action, start, is_group):
    final: list[str] = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        if action == "zip":
            await _edit(msg, tid, "🗜 Zipping...", uid, is_group)
            tm.set_status(tid, "processing")
            zp = path + ".zip"
            await make_zip(path, zp)
            final.append(zp)
        elif action == "unzip":
            if any(path.lower().endswith(e) for e in SUPPORTED_EXTRACT):
                await _edit(msg, tid, "📂 Extracting...", uid, is_group)
                tm.set_status(tid, "processing")
                extracted = await extract(path, dest_dir + "_ex",
                                          progress_msg=msg, task_id=tid)
                final.extend(f for f in extracted if os.path.isfile(f))
            else:
                final.append(path)
        else:
            final.append(path)

    if not final:
        raise FileNotFoundError("No files to upload after processing.")

    from bot import uploader_client, log_leech
    uclient = uploader_client()

    is_batch  = len(final) > 1
    batch_start = time.monotonic()
    total_bytes = sum(os.path.getsize(p) for p in final if os.path.isfile(p))

    # ── In groups: send file to user's PM, not the group ──
    if is_group:
        upload_chat_id = uid          # PM with the user
        # Notify group card that we're uploading to PM
        try:
            await msg.edit_text(
                group_task_card(uid, uploading_to_pm=True),
                parse_mode=enums.ParseMode.HTML,
                reply_markup=group_task_kb(uid),
            )
        except Exception:
            pass
        # Large files (premium/user_app) are safely relayed through
        # DUMP_CHANNEL inside uploader.py — see _send_relay()
        for p in final:
            if tm.is_cancelled(tid):
                break
            await upload_file(uclient, uid, p, tid, msg, uid,
                              origin_msg=message, is_group=True,
                              suppress_done_card=is_batch)
    else:
        for p in final:
            if tm.is_cancelled(tid):
                break
            await upload_file(uclient, message.chat.id, p, tid, msg, uid,
                              origin_msg=message, is_group=False,
                              suppress_done_card=is_batch)

    elapsed = time.monotonic() - start
    uname   = message.from_user.username or str(uid)
    fname   = os.path.basename(final[-1]) if final else ""
    await log_leech(uname, uid, fname, tid, elapsed)

    # ── Single summary card for batch (zip extract) uploads ──
    if is_batch and not tm.is_cancelled(tid):
        batch_elapsed = time.monotonic() - batch_start
        avg_spd       = total_bytes / max(batch_elapsed, 0.001)
        uname_fmt     = f"@{getattr(message.from_user, 'username', None) or uid}"
        summary = (
            f"╔═「 ✅ <b>UPLOAD COMPLETE</b> 」\n"
            f"║\n"
            f"║  📦 <b>{len(final)} files</b> uploaded\n"
            f"║\n"
            f"╠═「 📊 <b>STATS</b> 」\n"
            f"║  ➤ <b>Size</b>: <code>{human_size_pair(0, total_bytes)}</code>\n"
            f"║  ➤ <b>Speed</b>: <code>{human_speed(avg_spd)}</code>\n"
            f"║  ➤ <b>Time</b>: <code>{human_time(int(batch_elapsed))}</code>\n"
            f"║  ➤ <b>By</b>: {uname_fmt}\n"
            f"╚══════════════════════\n"
            f"  <i>{config.WATERMARK}</i>"
        )
        try:
            await msg.edit_text(summary, parse_mode=enums.ParseMode.HTML, reply_markup=None)
        except Exception:
            pass
        if is_group and message:
            try:
                await message.reply_text(summary, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass

    # Update group card to show done
    if is_group:
        await _refresh_group_card(uid)


# ── Torrent (handled by aria2c — see aria2_downloader.py) ────────────────

async def _handle_torrent(client, message, msg, info, dest_dir, tid, uid, action, start, is_group):
    """Route torrent/magnet to the aria2 downloader."""
    from bot.downloaders.aria2_downloader import torrent_download
    url   = info["url"]
    paths = await torrent_download(url, dest_dir, tid, msg, uid)
    paths = [paths] if isinstance(paths, str) else paths
    await _post_download(client, message, msg, paths, dest_dir, tid, uid, action, start, is_group)


async def _finish_torrent(client, message, msg, paths, dest_dir, tid, uid, action, start, is_group):
    try:
        await _post_download(client, message, msg, paths, dest_dir, tid, uid, action, start, is_group)
    except asyncio.CancelledError:
        await _cancel_msg(msg, tid)
    except Exception as e:
        await _error_msg(msg, tid, e)
    finally:
        tm.finish_task(tid)
        _cleanup(dest_dir)


def _build_file_kb(gid: str, files: list) -> InlineKeyboardMarkup:
    """File picker keyboard — kept for compatibility with callbacks."""
    from bot.utils.size_utils import human_size
    sel  = _selection.get(gid, set())
    rows = []
    for f in files[:20]:
        idx  = f.get("index", 0)
        name = os.path.basename(f.get("path", "")) or f"File {idx}"
        size = human_size(f.get("size", 0))
        icon = "✅" if idx in sel else "☐"
        rows.append([InlineKeyboardButton(
            f"{icon} {name[:32]} [{size}]", callback_data=f"tf_toggle:{gid}:{idx}"
        )])
    rows.append([
        InlineKeyboardButton("✅ All",       callback_data=f"tf_all:{gid}"),
        InlineKeyboardButton("☐ None",      callback_data=f"tf_none:{gid}"),
        InlineKeyboardButton("⬇️ Download", callback_data=f"tf_start:{gid}"),
    ])
    return InlineKeyboardMarkup(rows)

# ── Helpers ───────────────────────────────────────────────────

async def _edit(msg, tid, text, uid=None, is_group=False):
    # No-op: status card auto-refresh handles all display.
    # Just update task status text in task_manager so card reflects it.
    try:
        tm.set_status_text(tid, text)
    except Exception:
        pass


async def _cancel_msg(msg, tid, uid=None, task_name=""):
    card = cancel_card(tid, task_name)
    try:
        await msg.edit_text(card, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass
    if uid:
        try:
            from bot import app as _app
            await _app.send_message(uid, card, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


async def _error_msg(msg, tid, e, uid=None):
    card = error_card(tid, e)
    try:
        await msg.edit_text(card, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass
    if uid:
        try:
            from bot import app as _app
            await _app.send_message(uid, card, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ── Callbacks: torrent file selector ─────────────────────────

@Client.on_callback_query(filters.regex(r"^tf_toggle:"))
async def tf_toggle(client, cb: CallbackQuery):
    _, gid, idx_s = cb.data.split(":", 2)
    idx = int(idx_s)
    if gid not in _pending:
        return await cb.answer("Session expired. Send the link again.", show_alert=True)
    if cb.from_user.id != _pending[gid]["uid"]:
        return await cb.answer("Not your task.", show_alert=True)
    sel = _selection.setdefault(gid, set())
    if idx in sel:
        sel.discard(idx)
    else:
        sel.add(idx)
    try:
        files = []  # qBittorrent handles file listing via web UI
        await cb.message.edit_reply_markup(reply_markup=_build_file_kb(gid, files))
    except Exception:
        pass
    await cb.answer()


@Client.on_callback_query(filters.regex(r"^tf_all:"))
async def tf_all(client, cb: CallbackQuery):
    _, gid = cb.data.split(":", 1)
    if gid not in _pending:
        return await cb.answer("Session expired.", show_alert=True)
    if cb.from_user.id != _pending[gid]["uid"]:
        return await cb.answer("Not your task.", show_alert=True)
    files = []
    _selection[gid] = set(f["index"] for f in files)
    try:
        await cb.message.edit_reply_markup(reply_markup=_build_file_kb(gid, files))
    except Exception:
        pass
    await cb.answer("✅ All selected")


@Client.on_callback_query(filters.regex(r"^tf_none:"))
async def tf_none(client, cb: CallbackQuery):
    _, gid = cb.data.split(":", 1)
    if gid not in _pending:
        return await cb.answer("Session expired.", show_alert=True)
    if cb.from_user.id != _pending[gid]["uid"]:
        return await cb.answer("Not your task.", show_alert=True)
    files = []
    _selection[gid] = set()
    try:
        await cb.message.edit_reply_markup(reply_markup=_build_file_kb(gid, files))
    except Exception:
        pass
    await cb.answer("☐ All deselected")


@Client.on_callback_query(filters.regex(r"^tf_start:"))
async def tf_start(client, cb: CallbackQuery):
    _, gid = cb.data.split(":", 1)
    if gid not in _pending:
        return await cb.answer("Session expired. Send the link again.", show_alert=True)
    if cb.from_user.id != _pending[gid]["uid"]:
        return await cb.answer("Not your task.", show_alert=True)

    sel = _selection.get(gid, set())
    if not sel:
        return await cb.answer("⚠️ Select at least one file first.", show_alert=True)

    await cb.answer("⬇️ Starting download…")
    p     = _pending.pop(gid)
    _selection.pop(gid, None)
    try:
        pass  # qBittorrent handles selection via web UI
    except Exception:
        pass

    # Continue with actual download
    try:
        paths = []  # qBittorrent handles download
        await _finish_torrent(
            client, p["message"], p["msg"], paths,
            p["dest_dir"], p["tid"], p["uid"],
            p["action"], p["start"], p["is_group"],
        )
    except asyncio.CancelledError:
        await _cancel_msg(p["msg"], p["tid"])
    except Exception as e:
        await _error_msg(p["msg"], p["tid"], e)
    finally:
        tm.finish_task(p["tid"])
        _cleanup(p["dest_dir"])


def get_pending():        return _pending
def get_selection():      return _selection
def get_finish_torrent(): return _finish_torrent
def get_build_file_kb():  return _build_file_kb
