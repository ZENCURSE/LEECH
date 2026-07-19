from pyrogram import enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.database import users_db
import config


def is_authorized(message: Message) -> bool:
    uid = message.from_user.id if message.from_user else None
    if uid and (users_db.is_admin(uid) or uid == config.OWNER_ID):
        return True
    if config.AUTHORIZED_CHATS and message.chat.id in config.AUTHORIZED_CHATS:
        return True
    return False


async def auth_required(message: Message) -> bool:
    """Reply with error and return False if not authorized."""
    if not is_authorized(message):
        kb = None
        if getattr(config, "GROUP_LINK", ""):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("👥 Use the bot in our group", url=config.GROUP_LINK)
            ]])
        await message.reply_text(
            "❌ <b>You are not authorized to use this bot here.</b>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
        )
        return False
    return True
