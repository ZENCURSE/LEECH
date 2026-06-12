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
        f"<b>What I can do for you:</b>\n"
        f"  📥  Download from <b>1000+</b> websites\n"
        f"  📨  Telegram message links\n"
        f"  🧲  Torrent &amp; Magnet URIs\n"
        f"  🗜  Zip / Unzip with progress\n"
        f"  🖼  HD landscape auto-thumbnails\n"
        f"  🍪  Cookie support for premium sites\n"
        f"  🏷  Smart rename with variables\n\n"
        f"<b>Get started:</b>\n"
        f"  Just paste a link or use /d &lt;url&gt;\n\n"
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


HELP_TEXT = (
    "<b>📖 NXT HUB — Commands</b>\n\n"
    "╔═ 📥 <b>DOWNLOAD</b> ════════════╗\n"
    "  <code>/d &lt;url&gt;</code>        — download &amp; upload\n"
    "  <code>/d &lt;url&gt; zip</code>    — download → zip\n"
    "  <code>/d &lt;url&gt; unzip</code>  — download → extract\n"
    "  Or just <b>paste any link</b>\n"
    "╚═══════════════════════════╝\n\n"
    "╔═ ⚙️ <b>SETTINGS</b> ════════════╗\n"
    "  <code>/settings</code> — open your panel\n\n"
    "  <b>🏷 Rename variables:</b>\n"
    "  <code>{name}</code> <code>{size}</code> <code>{duration}</code>\n"
    "  <code>{language}</code> <code>{quality}</code> <code>{codec}</code>\n"
    "  <code>{audio}</code> <code>{fps}</code> <code>{date}</code>\n"
    "╚═══════════════════════════╝\n\n"
    "╔═ 📊 <b>TASKS</b> ══════════════╗\n"
    "  <code>/status</code>  — view your tasks\n"
    "  <code>/cancel &lt;id&gt;</code>  — cancel task\n"
    "  Or tap <b>Stop → /c1_XXXX</b>\n"
    "╚═══════════════════════════╝\n\n"
    "╔═ 🔗 <b>SOURCES</b> ═════════════╗\n"
    "  • YouTube, Vimeo, Dailymotion\n"
    "  • StreamTape, DoodStream\n"
    "  • Hubcloud, GDFlix, Pixeldrain\n"
    "  • Telegram message links\n"
    "  • Torrent &amp; Magnet URIs\n"
    "  • 1000+ sites via yt-dlp\n"
    "╚═══════════════════════════╝\n\n"
    "╔═ 👑 <b>ADMIN</b> ═══════════════╗\n"
    "  <code>/addowner</code> <code>/removeowner</code>\n"
    "  <code>/addadmin</code> <code>/removeadmin</code>\n"
    "  <code>/listusers</code>\n"
    "╚═══════════════════════════╝\n\n"
    f"━━━━━ <b>{config.WATERMARK}</b> ━━━━━"
)

ABOUT_TEXT = (
    "┌─────────────────────────┐\n"
    "│  🤖  <b>NXT HUB LEECH BOT</b>    │\n"
    "│       Version  <code>4.0.0</code>       │\n"
    "└─────────────────────────┘\n\n"
    "<b>⚙️ Powered by:</b>\n"
    "  • Pyrogram  —  Telegram MTProto\n"
    "  • yt-dlp    —  1000+ site support\n"
    "  • aria2     —  Torrent engine\n"
    "  • TMDB / Fanart  —  HD thumbnails\n"
    "  • FFmpeg    —  Media processing\n"
    "  • MongoDB   —  User data storage\n\n"
    "<b>✨ Features:</b>\n"
    "  📥  Multi-source downloader\n"
    "  📨  Telegram link support\n"
    "  🖼  HD landscape auto-thumb\n"
    "  🏷  Dynamic rename variables\n"
    "  🗜  Zip/unzip with progress\n"
    "  ✂️  Auto file splitting (4 GB)\n"
    "  🔒  Per-user auth &amp; settings\n"
    "  🗄  MongoDB persistent storage\n\n"
    f"━━━━━ <b>{config.WATERMARK}</b> ━━━━━"
)


def _help_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",  callback_data="nav_start"),
        InlineKeyboardButton("ℹ️ About", callback_data="nav_about"),
    ]])

def _about_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",  callback_data="nav_start"),
        InlineKeyboardButton("📖 Help",  callback_data="nav_help"),
    ]])


# ── "Start the bot first" prompt — shown in group when user hasn't PM'd ──
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
        f"<b>After that, just paste links here</b>\n"
        f"and I'll handle everything! 🚀",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "▶️  Start Bot Now",
                url=f"https://t.me/{bot_info.username}?start=from_group"
            )
        ]]),
        parse_mode=enums.ParseMode.HTML,
    )


# ── Handlers ──────────────────────────────────────────────────

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


# ── Nav callbacks ─────────────────────────────────────────────

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
        from bot.handlers.settings import _settings_text, _settings_kb
        await cb.message.edit_text(
            _settings_text(uid), reply_markup=_settings_kb(uid),
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
