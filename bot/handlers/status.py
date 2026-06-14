"""
Status handler + /c1_<tid> cancel alias command.
Refresh works for anyone (not just the task owner).
"""
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.core import task_manager as tm
from bot.utils.progress import status_message, group_task_kb
from bot.handlers._auth import auth_required

_status_msgs: dict[int, Message] = {}


def _status_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh All", callback_data=f"status_refresh:{uid}"),
    ]])


async def _refresh_loop(uid: int, msg: Message):
    """Auto-refresh the /status card every 5 s until all tasks finish."""
    for _ in range(240):          # max 20 min (240 × 5 s)
        await asyncio.sleep(5)
        tasks = {tid: data for tid, data in tm.all_tasks().items()
                 if data["user_id"] == uid}
        if not tasks:
            # All done — update card to reflect empty state then stop
            try:
                await msg.edit_text(
                    status_message(tasks),
                    reply_markup=_status_kb(uid),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            break
        try:
            await msg.edit_text(
                status_message(tasks),
                reply_markup=_status_kb(uid),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            # "message not modified" or flood — just skip this tick, keep looping
            pass
    _status_msgs.pop(uid, None)


@Client.on_message(filters.command("status") & (filters.private | filters.group))
async def cmd_status(client: Client, message: Message):
    if not await auth_required(message):
        return
    uid   = message.from_user.id
    tasks = {tid: data for tid, data in tm.all_tasks().items() if data["user_id"] == uid}
    msg   = await message.reply_text(
        status_message(tasks),
        reply_markup=_status_kb(uid),
        parse_mode=enums.ParseMode.HTML,
    )
    # Always start the loop — it will keep updating while tasks are active
    _status_msgs[uid] = msg
    asyncio.ensure_future(_refresh_loop(uid, msg))


@Client.on_message(filters.command("cancel") & (filters.private | filters.group))
async def cmd_cancel(client: Client, message: Message):
    if not await auth_required(message):
        return
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply_text(
            "❌ Usage: /cancel &lt;task_id&gt;", parse_mode=enums.ParseMode.HTML
        )
    tid = parts[1].split("@")[0].strip().upper()
    await _do_cancel(message, tid)


# ── /c1_<tid> — short cancel alias (shown as tappable link in task cards) ──
# The command is registered as /c1_XXXXXX (lowercase tid).
# When user taps it in the group chat, Telegram sends it as a message.

@Client.on_message(
    filters.regex(r"^/c1_[a-z0-9]+(?:@\w+)?") & (filters.private | filters.group)
)
async def cmd_cancel_alias(client: Client, message: Message):
    """Handles /c1_<tid> taps from the task card cancel link."""
    if not await auth_required(message):
        return
    raw = message.text.strip()          # e.g. /c1_abc123  or  /c1_abc123@BotName
    # Strip /c1_ prefix (4 chars), then cut off @BotName suffix if present
    tid = raw[4:].split("@")[0].upper()
    await _do_cancel(message, tid)


async def _do_cancel(message: Message, tid: str):
    task = tm.get_task(tid)
    if not task:
        return await message.reply_text(
            f"❌ Task <code>{tid}</code> not found.", parse_mode=enums.ParseMode.HTML
        )
    uid = message.from_user.id
    from bot.database import users_db
    if task["user_id"] != uid and not users_db.is_admin(uid):
        return await message.reply_text(
            "❌ You can only cancel your own tasks.", parse_mode=enums.ParseMode.HTML
        )
    if task["status"] == "cancelled":
        return await message.reply_text(
            f"⚠️ Task <code>{tid}</code> is already cancelled.", parse_mode=enums.ParseMode.HTML
        )
    tm.cancel_task(tid)
    gid = task.get("gid")
    if gid:
        try:
            from bot.core.downloader import torrent_remove
            await torrent_remove(gid)
        except Exception:
            pass
    await message.reply_text(
        f"🚫 Task <code>{tid}</code> cancelled.", parse_mode=enums.ParseMode.HTML
    )


# ── Refresh callback — works for ANYONE who taps it ─────────
@Client.on_callback_query(filters.regex(r"^status_refresh:"))
async def status_refresh_cb(client, cb):
    uid   = int(cb.data.split(":")[1])
    tasks = {tid: data for tid, data in tm.all_tasks().items()
             if data["user_id"] == uid}
    try:
        await cb.message.edit_text(
            status_message(tasks),
            reply_markup=_status_kb(uid),
            parse_mode=enums.ParseMode.HTML,
        )
        await cb.answer("🔄 Refreshed!")
    except Exception:
        await cb.answer()


@Client.on_callback_query(filters.regex(r"^grp_refresh:"))
async def grp_refresh_cb(client, cb):
    uid = int(cb.data.split(":")[1])
    # Anyone can refresh the group card (no ownership check)
    from bot.utils.progress import group_task_card
    try:
        await cb.message.edit_text(
            group_task_card(uid),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=group_task_kb(uid),
        )
        await cb.answer("🔄 Refreshed!")
    except Exception:
        await cb.answer()
