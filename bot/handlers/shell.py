"""
/shell — run a shell command (owners only).
Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML).
"""
from io import BytesIO
from asyncio import create_subprocess_shell
from asyncio.subprocess import PIPE

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from bot import LOGGER
from bot.database import users_db


@Client.on_message(
    filters.command(["shell", "sh"]) & (filters.private | filters.group)
)
async def cmd_shell(client: Client, message: Message):
    uid = message.from_user.id
    if not users_db.is_owner(uid):
        return await message.reply_text(
            "❌ <b>Owners only.</b>", parse_mode=enums.ParseMode.HTML
        )

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text(
            "❌ Usage: <code>/shell &lt;command&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    cmd = parts[1]
    msg = await message.reply_text(
        f"⚙️ <i>Running…</i>\n<code>{cmd}</code>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
        stdout, stderr = await proc.communicate()

        out = ""
        if stdout:
            out += f"<b>stdout</b>\n<code>{stdout.decode().strip()}</code>\n"
        if stderr:
            out += f"<b>stderr</b>\n<code>{stderr.decode().strip()}</code>\n"
        if not out:
            out = "<i>No output.</i>"

        LOGGER.info(f"[shell] {cmd} → rc={proc.returncode}")

        if len(out) > 3000:
            bio = BytesIO(out.encode())
            bio.name = "shell_output.txt"
            await msg.delete()
            await message.reply_document(bio, caption=f"<code>{cmd}</code>", parse_mode=enums.ParseMode.HTML)
        else:
            await msg.edit_text(out, parse_mode=enums.ParseMode.HTML)

    except Exception as e:
        LOGGER.error(f"[shell] {e}")
        await msg.edit_text(
            f"❌ Error: <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
