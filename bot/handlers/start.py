import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.database import users_db
import config


# ── Time-based greeting ───────────────────────────────────────
def _greet(user) -> str:
    h = datetime.datetime.now().hour
    if   5  <= h < 12: e = "🌅"
    elif 12 <= h < 17: e = "☀️"
    elif 17 <= h < 22: e = "🌆"
    else:               e = "🌙"
    return f"{e} <b>{user.first_name or 'there'}</b>"


# ══════════════════════════════════════════════════════════════
#  WELCOME
# ══════════════════════════════════════════════════════════════
def _welcome(user) -> str:
    from bot.core import task_manager as tm
    tasks       = [d for d in tm.all_tasks().values() if d.get("user_id") == user.id]
    task_hint   = f"  📊 You have <b>{len(tasks)}</b> task(s) running\n" if tasks else ""

    return (
        f"Hey, {_greet(user)}!\n\n"
        f"🚀 <b>NXT HUB LEECH BOT</b>  <code>v5</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  📥  Download from <b>1000+</b> sites\n"
        f"  🧲  Torrent &amp; magnet via aria2\n"
        f"  ✂️   Auto-split files up to <b>4 GB</b>\n"
        f"  🖼   Auto HD thumbnail card on every leech\n"
        f"  🏷   Smart rename with token variables\n"
        f"  📢  Dump channel forwarding\n\n"
        f"{task_hint}"
        f"<b>Paste a link or tap Help to get started.</b>\n\n"
        f"<i>{config.WATERMARK}</i>"
    )

def _welcome_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help",       callback_data="nav_help"),
            InlineKeyboardButton("⚙️ Settings",   callback_data="nav_settings"),
            InlineKeyboardButton("📊 My Tasks",   callback_data="nav_status"),
        ],
        [
            InlineKeyboardButton("👥 Use in Group", url=config.GROUP_LINK),
        ],
        [
            InlineKeyboardButton("ℹ️ About",      callback_data="nav_about"),
        ],
    ])


# ══════════════════════════════════════════════════════════════
#  HELP
# ══════════════════════════════════════════════════════════════
HELP_TEXT = (
    "📖 <b>NXT HUB — Command Reference</b>\n\n"

    "📥 <b>Download</b>\n"
    "<code>/d &lt;url&gt;</code>         — leech any URL\n"
    "<code>/d &lt;url&gt; zip</code>     — download → zip\n"
    "<code>/d &lt;url&gt; unzip</code>   — download → extract\n"
    "<code>/jdleech &lt;url&gt;</code>   — multi-host direct links\n"
    "<code>/torrent &lt;link&gt;</code>  — magnet/.torrent link or file\n"
    "<i>Or just paste a link directly.</i>\n\n"
    "<b>Sources:</b> YouTube · Mega.nz · MediaFire · GoFile\n"
    "TeraBox · OneDrive · Torrent/Magnet · 1000+ via yt-dlp\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "⚙️ <b>Settings</b>  <code>/settings</code>\n"
    "  📥 Cookies — yt-dlp premium access\n"
    "  📤 Thumbnail · Upload mode · Dump channel\n"
    "  🏷 Prefix · Suffix · Regex · Caption\n\n"
    "<b>Caption tokens:</b>  "
    "<code>{name}</code> <code>{size}</code> <code>{quality}</code> "
    "<code>{codec}</code> <code>{fps}</code> <code>{date}</code>\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "📊 <b>Tasks</b>\n"
    "<code>/status</code>         — view your active tasks\n"
    "<code>/cancel &lt;id&gt;</code>    — cancel a specific task\n\n"

    "👑 <b>Admin</b>\n"
    "<code>/admin</code>          — open admin panel\n"
    "<code>/listusers</code>      — list owners &amp; admins\n\n"

    f"<i>{config.WATERMARK}</i>"
)


# ══════════════════════════════════════════════════════════════
#  ABOUT
# ══════════════════════════════════════════════════════════════
ABOUT_TEXT = (
    "🤖 <b>NXT HUB LEECH BOT</b>  <code>v5.0.0</code>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    "⚙️ <b>Tech stack</b>\n"
    "  Pyrogram · yt-dlp · aria2 · FFmpeg\n"
    "  mega.py · TMDB/Fanart/iTunes · MongoDB\n\n"

    "✨ <b>What it does</b>\n"
    "  📥  1000+ site downloader (yt-dlp)\n"
    "  🔗  JDLeech — 30+ direct link hosts\n"
    "  🧲  Torrent &amp; magnet via aria2\n"
    "  🌐  Mega.nz — anonymous &amp; account\n"
    "  🎬  FFmpeg — H.264/H.265, CRF, presets,\n"
    "       audio, hardsub, watermark overlays\n"
    "  ✂️   Auto file splitting up to 4 GB\n"
    "       (4 GB with Telegram Premium session)\n"
    "  🖼   HD thumbnails — TMDB, Fanart, iTunes,\n"
    "       auto frame extraction fallback\n"
    "  🏷   Dynamic rename with token variables\n"
    "  🗜   Zip / unzip with live progress\n"
    "  📢  Dump channel forwarding\n"
    "  🔒  Per-user auth, settings &amp; cookies\n"
    "  🗄   MongoDB persistent user storage\n\n"

    f"<i>{config.WATERMARK}</i>"
)


# ══════════════════════════════════════════════════════════════
#  Keyboard helpers
# ══════════════════════════════════════════════════════════════
def _help_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",      callback_data="nav_start"),
        InlineKeyboardButton("ℹ️ About",     callback_data="nav_about"),
        InlineKeyboardButton("⚙️ Settings",  callback_data="nav_settings"),
    ]])

def _about_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Home",      callback_data="nav_start"),
        InlineKeyboardButton("📖 Help",      callback_data="nav_help"),
    ]])


# ══════════════════════════════════════════════════════════════
#  Group "start the bot first" prompt
# ══════════════════════════════════════════════════════════════
async def send_start_prompt(client, message: Message):
    bot_info = await client.get_me()
    user     = message.from_user
    await message.reply_text(
        f"👋 <b>Hey {user.first_name}!</b>\n\n"
        f"⚠️ <b>One-time setup needed</b>\n"
        f"Start me in PM once so I can send files to you directly.\n"
        f"After that, just paste links here freely! 🚀",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "▶️  Start Bot in PM",
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
    uid = message.from_user.id if message.from_user else None
    if uid and users_db.has_started(uid):
        # User already PM-started the bot — show welcome directly
        await message.reply_text(
            _welcome(message.from_user),
            reply_markup=_welcome_kb(),
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        # First time — ask them to start in PM once
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


# ══════════════════════════════════════════════════════════════
#  prog_status callback — "All Tasks" button on task cards
# ══════════════════════════════════════════════════════════════
@Client.on_callback_query(filters.regex(r"^prog_status:"))
async def prog_status_cb(client, cb):
    from bot.core import task_manager as tm
    from bot.utils.progress import status_message
    from bot.handlers.status import _status_kb
    uid   = cb.from_user.id
    tasks = {t: d for t, d in tm.all_tasks().items() if d["user_id"] == uid}
    try:
        await cb.message.edit_text(
            status_message(tasks), reply_markup=_status_kb(uid),
            parse_mode=enums.ParseMode.HTML,
        )
        await cb.answer()
    except Exception:
        await cb.answer("No change.")
