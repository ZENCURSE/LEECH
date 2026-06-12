from pyrogram import Client, filters, enums
from pyrogram.types import Message
from bot.database import users_db
import config


async def _resolve_target(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    parts = message.text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    await message.reply_text("❌ Provide a user ID or reply to a user's message.")
    return None


@Client.on_message(filters.command("addowner") & (filters.private | filters.group))
async def cmd_add_owner(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this command.")
    target = await _resolve_target(message)
    if target is None:
        return
    if users_db.add_owner(target):
        await message.reply_text(f"✅ <code>{target}</code> added as owner.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"⚠️ <code>{target}</code> is already an owner.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("removeowner") & (filters.private | filters.group))
async def cmd_remove_owner(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this command.")
    target = await _resolve_target(message)
    if target is None:
        return
    if target == config.OWNER_ID:
        return await message.reply_text("❌ Cannot remove the main owner.")
    if users_db.remove_owner(target):
        await message.reply_text(f"✅ <code>{target}</code> removed from owners.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"⚠️ <code>{target}</code> is not an owner.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("addadmin") & (filters.private | filters.group))
async def cmd_add_admin(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this command.")
    target = await _resolve_target(message)
    if target is None:
        return
    if users_db.add_admin(target):
        await message.reply_text(f"✅ <code>{target}</code> added as admin.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"⚠️ <code>{target}</code> is already an admin.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("removeadmin") & (filters.private | filters.group))
async def cmd_remove_admin(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_owner(uid):
        return await message.reply_text("❌ Only owners can use this command.")
    target = await _resolve_target(message)
    if target is None:
        return
    if users_db.remove_admin(target):
        await message.reply_text(f"✅ <code>{target}</code> removed from admins.", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"⚠️ <code>{target}</code> is not an admin.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("listusers") & (filters.private | filters.group))
async def cmd_list_users(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    if not uid or not users_db.is_admin(uid):
        return await message.reply_text("❌ Only admins/owners can use this command.")
    data = users_db.list_users()
    owners_str = "\n".join(f"  • <code>{u}</code>" for u in data["owners"])
    admins_str = "\n".join(f"  • <code>{u}</code>" for u in data["admins"]) or "  None"
    text = (
        f"<b>👑 Owners</b>\n{owners_str}\n\n"
        f"<b>🛡 Admins</b>\n{admins_str}"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
