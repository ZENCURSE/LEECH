"""
Admin panel — NXT HUB v5
Commands: /admin  /addowner  /removeowner  /addadmin  /removeadmin  /listusers  /broadcast
"""
import asyncio
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from bot.database import users_db
import config

_DIV = "━" * 28


# ── Resolve target user ───────────────────────────────────────
async def _resolve_target(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    parts = message.text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    await message.reply_text(
        "❌ Provide a user ID or reply to a user's message.",
        parse_mode=enums.ParseMode.HTML,
    )
    return None


# ══════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ══════════════════════════════════════════════════════════════

def _admin_panel_text() -> str:
    from bot.core import task_manager as tm
    data   = users_db.list_users()
    s      = tm.stats() if hasattr(tm, "stats") else {}
    active = s.get("active", 0)
    queued = s.get("queued", 0)

    return (
        f"👑 <b>Admin Panel</b>\n"
        f"{_DIV}\n\n"
        f"👥 <b>Users</b>\n"
        f"   Owners: <b>{len(data['owners'])}</b>   "
        f"Admins: <b>{len(data['admins'])}</b>\n\n"
        f"📊 <b>Tasks</b>\n"
        f"   🟢 Active: <b>{active}</b>   🕐 Queued: <b>{queued}</b>\n\n"
        f"<i>Use the buttons below or reply to a user's message and run a command.</i>"
    )

def _admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👑 Add Owner",     callback_data="adm_prompt:addowner"),
            InlineKeyboardButton("🗑 Remove Owner",  callback_data="adm_prompt:removeowner"),
        ],
        [
            InlineKeyboardButton("🛡 Add Admin",     callback_data="adm_prompt:addadmin"),
            InlineKeyboardButton("🗑 Remove Admin",  callback_data="adm_prompt:removeadmin"),
        ],
        [
            InlineKeyboardButton("📋 List Users",    callback_data="adm_listusers"),
        ],
        [
            InlineKeyboardButton("✖️ Close",         callback_data="adm_close"),
        ],
    ])


@Client.on_message(filters.command("admin") & (filters.private | filters.group))
async def cmd_admin(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_admin(uid):
        return await message.reply_text(
            "❌ Only admins and owners can access the admin panel.",
            parse_mode=enums.ParseMode.HTML,
        )
    await message.reply_text(
        _admin_panel_text(),
        reply_markup=_admin_panel_kb(),
        parse_mode=enums.ParseMode.HTML,
    )


# ── Admin panel callbacks ─────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^adm_"))
async def admin_cb(client, cb):
    uid  = cb.from_user.id
    data = cb.data

    if not users_db.is_admin(uid):
        return await cb.answer("❌ Admins only.", show_alert=True)

    if data == "adm_listusers":
        info   = users_db.list_users()
        owners = "\n".join(f"  👑 <code>{u}</code>" for u in info["owners"]) or "  —"
        admins = "\n".join(f"  🛡 <code>{u}</code>" for u in info["admins"]) or "  —"
        text = (
            f"👥 <b>User List</b>\n{_DIV}\n\n"
            f"<b>Owners ({len(info['owners'])})</b>\n{owners}\n\n"
            f"<b>Admins ({len(info['admins'])})</b>\n{admins}"
        )
        try:
            await cb.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Back", callback_data="adm_back"),
                ]]),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return await cb.answer()

    if data == "adm_back":
        try:
            await cb.message.edit_text(
                _admin_panel_text(),
                reply_markup=_admin_panel_kb(),
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return await cb.answer()

    if data == "adm_close":
        await cb.message.delete()
        return

    if data.startswith("adm_prompt:"):
        action = data.split(":", 1)[1]
        labels = {
            "addowner":    "Add Owner",
            "removeowner": "Remove Owner",
            "addadmin":    "Add Admin",
            "removeadmin": "Remove Admin",
        }
        label = labels.get(action, action)
        await cb.answer(f"Send /{action} <user_id> or reply to a user and run /{action}.", show_alert=True)
        return

    await cb.answer()


# ══════════════════════════════════════════════════════════════
#  Owner / Admin management commands
# ══════════════════════════════════════════════════════════════

@Client.on_message(filters.command("addowner") & (filters.private | filters.group))
async def cmd_add_owner(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this.")
    target = await _resolve_target(message)
    if target is None: return
    if users_db.add_owner(target):
        await message.reply_text(
            f"✅ <code>{target}</code> is now an <b>Owner</b>.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"⚠️ <code>{target}</code> is already an owner.",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("removeowner") & (filters.private | filters.group))
async def cmd_remove_owner(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this.")
    target = await _resolve_target(message)
    if target is None: return
    if target == config.OWNER_ID:
        return await message.reply_text("❌ Cannot remove the main owner.")
    if users_db.remove_owner(target):
        await message.reply_text(
            f"✅ <code>{target}</code> removed from owners.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"⚠️ <code>{target}</code> is not an owner.",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("addadmin") & (filters.private | filters.group))
async def cmd_add_admin(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this.")
    target = await _resolve_target(message)
    if target is None: return
    if users_db.add_admin(target):
        await message.reply_text(
            f"✅ <code>{target}</code> is now an <b>Admin</b>.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"⚠️ <code>{target}</code> is already an admin.",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("removeadmin") & (filters.private | filters.group))
async def cmd_remove_admin(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this.")
    target = await _resolve_target(message)
    if target is None: return
    if users_db.remove_admin(target):
        await message.reply_text(
            f"✅ <code>{target}</code> removed from admins.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply_text(
            f"⚠️ <code>{target}</code> is not an admin.",
            parse_mode=enums.ParseMode.HTML,
        )


@Client.on_message(filters.command("listusers") & (filters.private | filters.group))
async def cmd_list_users(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_admin(uid):
        return await message.reply_text("❌ Only admins/owners can use this.")
    data   = users_db.list_users()
    owners = "\n".join(f"  👑 <code>{u}</code>" for u in data["owners"]) or "  —"
    admins = "\n".join(f"  🛡 <code>{u}</code>" for u in data["admins"]) or "  —"
    await message.reply_text(
        f"👥 <b>User List</b>\n{_DIV}\n\n"
        f"<b>Owners ({len(data['owners'])})</b>\n{owners}\n\n"
        f"<b>Admins ({len(data['admins'])})</b>\n{admins}",
        parse_mode=enums.ParseMode.HTML,
    )


# ══════════════════════════════════════════════════════════════
#  BROADCAST
# ══════════════════════════════════════════════════════════════
_pending_broadcast: dict[int, tuple[int, int]] = {}   # admin_uid -> (from_chat_id, message_id)


@Client.on_message(filters.command("broadcast") & (filters.private | filters.group))
async def cmd_broadcast(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_admin(uid):
        return await message.reply_text("❌ Only admins/owners can use this.")

    src = message.reply_to_message
    text_arg = message.text.split(None, 1)
    if not src and len(text_arg) < 2:
        return await message.reply_text(
            "📢 <b>Broadcast</b>\n"
            "Reply to any message (text, photo, video, document…) with "
            "<code>/broadcast</code>, or send <code>/broadcast &lt;text&gt;</code>.\n\n"
            "It'll be copied — with formatting/media intact — to every user "
            "who has started the bot.",
            parse_mode=enums.ParseMode.HTML,
        )

    total = len(users_db.get_all_started_users())
    if total == 0:
        return await message.reply_text("⚠️ No users to broadcast to yet.")

    if src:
        _pending_broadcast[uid] = (src.chat.id, src.id)
    else:
        # Plain text with no reply — send it to ourselves privately first
        # so we have a message to copy from (preserves the same code path)
        sent = await client.send_message(uid, text_arg[1])
        _pending_broadcast[uid] = (sent.chat.id, sent.id)

    await message.reply_text(
        f"📢 <b>Broadcast to {total} user{'s' if total != 1 else ''}?</b>\n\n"
        f"This sends immediately once confirmed — there's no undo.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data="bcast_go"),
            InlineKeyboardButton("❌ Cancel",  callback_data="bcast_no"),
        ]]),
    )


@Client.on_callback_query(filters.regex(r"^bcast_(go|no)$"))
async def broadcast_cb(client: Client, cb):
    uid = cb.from_user.id
    if not users_db.is_admin(uid):
        return await cb.answer("❌ Not authorized.", show_alert=True)

    pending = _pending_broadcast.get(uid)
    if not pending:
        return await cb.answer("⚠️ Nothing pending — start over with /broadcast.", show_alert=True)

    if cb.data == "bcast_no":
        _pending_broadcast.pop(uid, None)
        await cb.answer("Cancelled.")
        return await cb.message.edit_text("❌ Broadcast cancelled.")

    _pending_broadcast.pop(uid, None)
    from_chat_id, message_id = pending
    users = users_db.get_all_started_users()
    total = len(users)
    await cb.answer("Starting…")

    sent = failed = 0
    start = time.monotonic()
    last_edit = 0.0

    for i, target_uid in enumerate(users, 1):
        try:
            await client.copy_message(target_uid, from_chat_id, message_id)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await client.copy_message(target_uid, from_chat_id, message_id)
                sent += 1
            except Exception:
                failed += 1
        except Exception as e:
            failed += 1
            # Permanently gone (blocked the bot, deleted account, etc.) —
            # drop them so future broadcasts don't waste a send on them
            err = type(e).__name__
            if any(k in err for k in ("UserIsBlocked", "InputUserDeactivated",
                                       "UserDeactivated", "PeerIdInvalid")):
                users_db.remove_started(target_uid)

        now = time.monotonic()
        if now - last_edit > 3 or i == total:
            last_edit = now
            pct = i / total * 100
            try:
                await cb.message.edit_text(
                    f"📢 <b>Broadcasting…</b>\n\n"
                    f"「{'🟩' * int(pct // 10)}{'⬜' * (10 - int(pct // 10))}」  {pct:.0f}%\n\n"
                    f"✅ Sent: <b>{sent}</b>   ❌ Failed: <b>{failed}</b>   "
                    f"👥 Total: <b>{total}</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)   # gentle pacing, well under Telegram's rate limits

    elapsed = time.monotonic() - start
    await cb.message.edit_text(
        f"✅ <b>Broadcast complete</b>\n\n"
        f"Sent: <b>{sent}</b>   Failed: <b>{failed}</b>   "
        f"Time: <b>{elapsed:.0f}s</b>",
        parse_mode=enums.ParseMode.HTML,
    )
