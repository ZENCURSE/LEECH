import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.database import users_db
import config


# ── Time-based greeting ───────────────────────────────────────
def _greet(user) -> str:
    h = datetime.datetime.now().hour
    if   5  <= h < 12: g, e = "Good morning",   "🌅"
    elif 12 <= h < 17: g, e = "Good afternoon",  "☀️"
    elif 17 <= h < 22: g, e = "Good evening",    "🌆"
    else:               g, e = "Good night",      "🌙"
    return f"{e}  <b>{g}, {user.first_name or 'there'}!</b>"


# ══════════════════════════════════════════════════════════════
#  WELCOME
# ══════════════════════════════════════════════════════════════
def _welcome(user) -> str:
    return (
        f"{_greet(user)}\n\n"
        f"🚀 <b>NXT HUB LEECH BOT</b>\n"
        f"<i>Fast · Smart · Free</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📥  Download from <b>1000+</b> sites\n"
        f"🎬  FFmpeg encode with custom presets\n"
        f"🧲  Torrent &amp; magnet support\n"
        f"✂️   Auto-split up to <b>4 GB</b>\n"
        f"🖼   HD auto-thumbnails (TMDB/Fanart)\n"
        f"🏷   Smart rename with token variables\n\n"
        f"<b>→ Paste a link or use /d to get started</b>\n\n"
        f"<i>{config.WATERMARK}</i>"
    )

def _welcome_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help",      callback_data="nav_help"),
            InlineKeyboardButton("⚙️ Settings",  callback_data="nav_settings"),
        ],
        [
            InlineKeyboardButton("ℹ️ About",     callback_data="nav_about"),
            InlineKeyboardButton("📊 My Tasks",  callback_data="nav_status"),
        ],
    ])


# ══════════════════════════════════════════════════════════════
#  HELP
# ══════════════════════════════════════════════════════════════
HELP_TEXT = (
    "📖 <b>NXT HUB — Command Guide</b>\n\n"

    "━━  📥 <b>Download</b>  ━━━━━━━━━━━━━━━━━━━\n"
    "<code>/d &lt;url&gt;</code>           — leech any URL\n"
    "<code>/d &lt;url&gt; zip</code>       — download → zip\n"
    "<code>/d &lt;url&gt; unzip</code>     — download → extract\n"
    "<code>/jdleech &lt;url&gt;</code>     — multi-host direct links\n"
    "Or just <b>paste a link</b> directly.\n\n"

    "<b>Supported:</b> YouTube · MediaFire · Mega.nz\n"
    "GoFile · TeraBox · Torrent/Magnet · Telegram\n"
    "OneDrive · WeTransfer · 1000+ via yt-dlp\n\n"

    "━━  🎬 <b>Encoding</b>  ━━━━━━━━━━━━━━━━━━\n"
    "<code>/encode</code>         — reply to a video to encode\n"
    "<code>/encurl &lt;url&gt;</code>   — download + encode\n"
    "<code>/encsub</code>         — hardsub a video\n"
    "<code>/encset</code>         — open encode settings\n"
    "<code>/vset</code>           — view current settings\n\n"
    "<b>Options:</b> H.264/H.265 · CRF · Preset · FPS\n"
    "Resolution · Audio codec · Watermark · Subs\n\n"

    "━━  ⚙️ <b>Settings</b>  ━━━━━━━━━━━━━━━━━━\n"
    "<code>/settings</code>       — open your settings panel\n\n"
    "📥 <b>Download:</b>  cookies.txt for premium access\n"
    "📤 <b>Upload:</b>    thumbnail · mode · dump channel\n"
    "🎬 <b>Encoding:</b>  full FFmpeg settings\n"
    "🏷 <b>Rename:</b>    prefix · suffix · regex · caption\n\n"
    "<b>Caption tokens:</b>\n"
    "<code>{name}</code> <code>{size}</code> <code>{quality}</code> "
    "<code>{language}</code> <code>{codec}</code> <code>{fps}</code> <code>{date}</code>\n\n"

    "━━  📊 <b>Tasks</b>  ━━━━━━━━━━━━━━━━━━━━\n"
    "<code>/status</code>         — view your active tasks\n"
    "<code>/cancel &lt;id&gt;</code>    — cancel a task\n\n"

    "━━  👑 <b>Admin</b>  ━━━━━━━━━━━━━━━━━━━━\n"
    "<code>/addowner</code>  <code>/removeowner</code>  "
    "<code>/addadmin</code>  <code>/removeadmin</code>  "
    "<code>/listusers</code>\n\n"

    f"<i>{config.WATERMARK}</i>"
)


# ══════════════════════════════════════════════════════════════
#  ABOUT
# ══════════════════════════════════════════════════════════════
ABOUT_TEXT = (
    "🤖 <b>NXT HUB LEECH BOT</b>  <code>v5.0.0</code>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "⚙️ <b>Built with:</b>\n"
    "  Pyrogram · yt-dlp · aria2 · FFmpeg\n"
    "  mega.py · TMDB/Fanart · MongoDB\n\n"

    "✨ <b>Features:</b>\n"
    "  📥  1000+ site downloader via yt-dlp\n"
    "  🔗  JDLeech — 30+ direct link hosters\n"
    "  🧲  Torrent &amp; magnet via aria2\n"
    "  🌐  Mega.nz (anonymous &amp; account)\n"
    "  🎬  FFmpeg — H.264/H.265, CRF, subs,\n"
    "       watermark, audio, custom presets\n"
    "  ✂️   Auto file splitting up to 4 GB\n"
    "  🖼   HD thumbnails — TMDB, Fanart, iTunes\n"
    "  🏷   Dynamic rename with token variables\n"
    "  🗜   Zip / unzip with live progress\n"
    "  📢  Dump channel forwarding\n"
    "  🔒  Per-user auth, settings &amp; cookies\n"
    "  🗄   MongoDB persistent storage\n\n"

    f"<i>{config.WATERMARK}</i>"
)


# ══════════════════════════════════════════════════════════════
#  Keyboard helpers
# ══════════════════════════════════════════════════════════════
def _help_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",   callback_data="nav_start"),
        InlineKeyboardButton("ℹ️ About",  callback_data="nav_about"),
    ]])

def _about_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",   callback_data="nav_start"),
        InlineKeyboardButton("📖 Help",   callback_data="nav_help"),
    ]])


# ══════════════════════════════════════════════════════════════
#  Group "start the bot first" prompt
# ══════════════════════════════════════════════════════════════
async def send_start_prompt(client, message: Message):
    bot_info = await client.get_me()
    user     = message.from_user
    await message.reply_text(
        f"👋 <b>Hey {user.first_name}!</b>\n\n"
        f"⚠️ <b>One-time setup needed</b>\n\n"
        f"Start me in PM <b>once</b> so I can send files\n"
        f"directly to you — then paste links here freely.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "▶️  Start Bot",
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
#  Navigation callbacks
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
