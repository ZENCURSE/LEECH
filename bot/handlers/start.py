import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.database import users_db
import config


def _greet(user) -> str:
    h = datetime.datetime.now().hour
    if   5  <= h < 12: g = "🌅 Good morning"
    elif 12 <= h < 17: g = "☀️ Good afternoon"
    elif 17 <= h < 22: g = "🌆 Good evening"
    else:               g = "🌙 Good night"
    return f"{g}, <b>{user.first_name or 'there'}</b>!"


def _welcome(user) -> str:
    return (
        f"{_greet(user)}\n\n"
        f"┌─────────────────────────┐\n"
        f"│  🚀  <b>NXT HUB LEECH BOT</b>    │\n"
        f"│  ⚡  Fast · Smart · Free  │\n"
        f"└─────────────────────────┘\n\n"
        f"<b>What I can do:</b>\n"
        f"  📥  Download from <b>1000+</b> sites\n"
        f"  🔗  JDLeech — multi-host direct links\n"
        f"  🧲  Torrent &amp; Magnet support\n"
        f"  🎬  FFmpeg encode with custom settings\n"
        f"  ✂️  Auto-split files up to 4 GB\n"
        f"  🖼  HD auto-thumbnails (TMDB/Fanart)\n"
        f"  🏷  Smart rename with token variables\n\n"
        f"<b>Get started →</b> paste a link or use /d\n\n"
        f"━━━━━ <b>{config.WATERMARK}</b> ━━━━━"
    )


def _welcome_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help",     callback_data="nav_help"),
            InlineKeyboardButton("⚙️ Settings", callback_data="nav_settings"),
        ],
        [
            InlineKeyboardButton("ℹ️ About",    callback_data="nav_about"),
            InlineKeyboardButton("📊 My Tasks", callback_data="nav_status"),
        ],
    ])


# ══════════════════════════════════════════════════════════════
#  HELP TEXT
# ══════════════════════════════════════════════════════════════

HELP_TEXT = (
    "<b>📖 NXT HUB — Full Command Guide</b>\n\n"

    "╔═ 📥 <b>DOWNLOAD</b> ══════════════════╗\n"
    "  <code>/d &lt;url&gt;</code>         — leech any URL\n"
    "  <code>/d &lt;url&gt; zip</code>     — download → zip\n"
    "  <code>/d &lt;url&gt; unzip</code>   — download → extract\n"
    "  <code>/jdleech &lt;url&gt;</code>   — multi-host direct links\n"
    "  Or just <b>paste any link</b> directly\n\n"
    "  <b>Supported sources:</b>\n"
    "  • YouTube, Vimeo, Dailymotion, Twitch\n"
    "  • MediaFire, PixelDrain, BuzzHeavier\n"
    "  • GoFile, TeraBox, 1Fichier, KrakenFiles\n"
    "  • WeTransfer, OneDrive, Yandex Disk\n"
    "  • Streamtape, DoodStream, FileLions\n"
    "  • Mega.nz (anonymous + account)\n"
    "  • Torrent files &amp; Magnet URIs\n"
    "  • Telegram message links\n"
    "  • 1000+ sites via yt-dlp\n"
    "╚══════════════════════════════════╝\n\n"

    "╔═ 🎬 <b>ENCODING</b> ══════════════════╗\n"
    "  <code>/encode</code>       — reply to a video to encode\n"
    "  <code>/encurl &lt;url&gt;</code> — download + encode URL\n"
    "  <code>/encsub</code>       — video + subtitle → hardsub\n"
    "  <code>/encsub &lt;v_url&gt; &lt;s_url&gt;</code> — both as URLs\n"
    "  <code>/encset</code>       — open encode settings panel\n"
    "  <code>/vset</code>         — view current encode settings\n\n"
    "  <b>Configurable via /encset:</b>\n"
    "  • Codec: H.264 / H.265\n"
    "  • CRF quality · Preset · Resolution\n"
    "  • FPS · Audio codec · Bitrate\n"
    "  • Hardsub · Softsub · Watermark\n"
    "╚══════════════════════════════════╝\n\n"

    "╔═ ⚙️ <b>SETTINGS</b> ═══════════════════╗\n"
    "  <code>/settings</code> — open your personal panel\n\n"
    "  <b>📥 Download</b>\n"
    "    🍪 Cookies.txt for yt-dlp premium access\n\n"
    "  <b>📤 Upload</b>\n"
    "    🖼 Custom thumbnail (photo or file)\n"
    "    📄 Upload mode: Media / Document\n"
    "    📢 Dump channel forwarding\n\n"
    "  <b>🎬 Encoding</b>\n"
    "    Full FFmpeg settings panel\n\n"
    "  <b>🏷 Rename</b>\n"
    "    Prefix · Suffix · Regex · Caption\n\n"
    "  <b>Caption/rename tokens:</b>\n"
    "  <code>{name}</code> <code>{size}</code> <code>{quality}</code> "
    "<code>{language}</code>\n"
    "  <code>{codec}</code> <code>{audio}</code> <code>{fps}</code> "
    "<code>{date}</code>\n"
    "╚══════════════════════════════════╝\n\n"

    "╔═ 📊 <b>TASKS</b> ══════════════════════╗\n"
    "  <code>/status</code>         — view active tasks\n"
    "  <code>/cancel &lt;id&gt;</code>   — cancel a task\n"
    "  Or tap ❌ Cancel on the task card\n"
    "╚══════════════════════════════════╝\n\n"

    "╔═ 👑 <b>ADMIN</b> ═══════════════════════╗\n"
    "  <code>/addowner</code>    <code>/removeowner</code>\n"
    "  <code>/addadmin</code>    <code>/removeadmin</code>\n"
    "  <code>/listusers</code>\n"
    "╚══════════════════════════════════╝\n\n"

    f"━━━━━ <b>{config.WATERMARK}</b> ━━━━━"
)


# ══════════════════════════════════════════════════════════════
#  ABOUT TEXT
# ══════════════════════════════════════════════════════════════

ABOUT_TEXT = (
    "┌─────────────────────────┐\n"
    "│  🤖  <b>NXT HUB LEECH BOT</b>    │\n"
    "│       Version  <code>5.0.0</code>       │\n"
    "└─────────────────────────┘\n\n"

    "<b>⚙️ Powered by:</b>\n"
    "  • <b>Pyrogram</b>   — Telegram MTProto client\n"
    "  • <b>yt-dlp</b>     — 1000+ site downloader\n"
    "  • <b>aria2</b>      — Torrent / magnet engine\n"
    "  • <b>FFmpeg</b>     — Encoding &amp; media processing\n"
    "  • <b>mega.py</b>    — Mega.nz downloads\n"
    "  • <b>TMDB / Fanart</b> — HD auto-thumbnails\n"
    "  • <b>MongoDB</b>    — User data &amp; settings\n\n"

    "<b>✨ Features:</b>\n"
    "  📥  Multi-source downloader (1000+ sites)\n"
    "  🔗  JDLeech — 30+ direct link hosters\n"
    "  🧲  Torrent &amp; magnet support via aria2\n"
    "  🌐  Mega.nz anonymous &amp; account login\n"
    "  🎬  FFmpeg encoding — H.264/H.265, CRF,\n"
    "       preset, resolution, audio, subs,\n"
    "       watermark overlay\n"
    "  ✂️  Auto file splitting up to 4 GB\n"
    "       (4 GB with Premium session)\n"
    "  🖼  HD landscape thumbnails — TMDB,\n"
    "       Fanart.tv, iTunes, auto-frame extract\n"
    "  🏷  Dynamic rename with token variables\n"
    "  🗜  Zip / unzip with live progress\n"
    "  📢  Dump channel forwarding\n"
    "  🔒  Per-user auth, settings &amp; cookies\n"
    "  🗄  MongoDB persistent user storage\n\n"

    f"━━━━━ <b>{config.WATERMARK}</b> ━━━━━"
)


# ══════════════════════════════════════════════════════════════
#  Keyboard helpers
# ══════════════════════════════════════════════════════════════

def _help_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",  callback_data="nav_start"),
        InlineKeyboardButton("ℹ️ About", callback_data="nav_about"),
    ]])

def _about_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home", callback_data="nav_start"),
        InlineKeyboardButton("📖 Help", callback_data="nav_help"),
    ]])


# ── "Start the bot first" prompt in groups ────────────────────

async def send_start_prompt(client, message: Message):
    bot_info = await client.get_me()
    user     = message.from_user
    await message.reply_text(
        f"👋 <b>Hey {user.first_name}!</b>\n\n"
        f"┌──────────────────────┐\n"
        f"│  ⚠️  <b>One-time setup needed</b>  │\n"
        f"└──────────────────────┘\n\n"
        f"Start the bot in PM <b>once</b> so I can\n"
        f"send files directly to you.\n\n"
        f"<b>After that, just paste links here!</b> 🚀",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "▶️  Start Bot Now",
                url=f"https://t.me/{bot_info.username}?start=from_group"
            )
        ]]),
        parse_mode=enums.ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════
#  Command handlers
# ══════════════════════════════════════════════════════════════

@Client.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message: Message):
    users_db.mark_started(message.from_user.id)
    await message.reply_text(
        _welcome(message.from_user),
        reply_markup=_welcome_kb(),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


@Client.on_message(filters.command("start") & filters.group)
async def cmd_start_group(client: Client, message: Message):
    await send_start_prompt(client, message)


@Client.on_message(filters.command("help") & (filters.private | filters.group))
async def cmd_help(client: Client, message: Message):
    await message.reply_text(
        HELP_TEXT, reply_markup=_help_kb(),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


@Client.on_message(filters.command("about") & (filters.private | filters.group))
async def cmd_about(client: Client, message: Message):
    await message.reply_text(
        ABOUT_TEXT, reply_markup=_about_kb(),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ══════════════════════════════════════════════════════════════
#  Navigation callbacks (inline buttons)
# ══════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^nav_"))
async def nav_cb(client, cb):
    uid    = cb.from_user.id
    action = cb.data.split("_", 1)[1]

    if action == "start":
        await cb.message.edit_text(
            _welcome(cb.from_user), reply_markup=_welcome_kb(),
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )
    elif action == "help":
        await cb.message.edit_text(
            HELP_TEXT, reply_markup=_help_kb(),
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )
    elif action == "about":
        await cb.message.edit_text(
            ABOUT_TEXT, reply_markup=_about_kb(),
            parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True,
        )
    elif action == "settings":
        from bot.handlers.settings import _main_text, _main_settings_kb
        await cb.message.edit_text(
            _main_text(uid), reply_markup=_main_settings_kb(),
            parse_mode=enums.ParseMode.HTML,
        )
    elif action == "status":
        from bot.core import task_manager as tm
        from bot.utils.progress import status_message
        from bot.handlers.status import _status_kb
        tasks = {t: d for t, d in tm.all_tasks().items() if d["user_id"] == uid}
        await cb.message.edit_text(
            status_message(tasks), reply_markup=_status_kb(uid),
            parse_mode=enums.ParseMode.HTML,
        )
    await cb.answer()
