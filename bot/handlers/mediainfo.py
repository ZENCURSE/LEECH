"""
/mediainfo — generate MediaInfo for a replied file or a direct URL.
Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML).
"""
import os
import re
from shlex import split as sh_split
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE

import aiohttp
import aiofiles
import aiofiles.os

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from bot import LOGGER
from bot.handlers._auth import auth_required
from bot.utils.telegraph_utils import telegraph

_MEDIAINFO_DIR = "/tmp/nxtl_mediainfo"
_SECTION_ICONS = {"General": "🗒", "Video": "🎞", "Audio": "🔊", "Text": "🔠", "Menu": "🗃"}


async def _ensure_dir():
    os.makedirs(_MEDIAINFO_DIR, exist_ok=True)


async def _run_mediainfo(path: str) -> str:
    proc = await create_subprocess_exec(
        "mediainfo", path, stdout=PIPE, stderr=PIPE
    )
    stdout, _ = await proc.communicate()
    return stdout.decode()


def _parse_mediainfo(out: str, file_size: int) -> str:
    tc, trigger = "", False
    size_line = f"File size                                 : {file_size / (1024*1024):.2f} MiB"
    for line in out.split("\n"):
        for section, emoji in _SECTION_ICONS.items():
            if line.startswith(section):
                trigger = True
                if not line.startswith("General"):
                    tc += "</pre><br>"
                tc += f"<h4>{emoji} {line.replace('Text', 'Subtitle')}</h4>"
                break
        if line.startswith("File size"):
            line = size_line
        if trigger:
            tc += "<br><pre>"
            trigger = False
        else:
            tc += line + "\n"
    tc += "</pre><br>"
    return tc


async def _gen_mediainfo_from_url(url: str, msg: Message):
    m = re.search(r".+/(.+)", url)
    if not m:
        return await msg.edit_text("❌ Could not extract filename from URL.")
    filename = m.group(1)
    dest     = os.path.join(_MEDIAINFO_DIR, filename)
    headers  = {"user-agent": "Mozilla/5.0"}
    file_size = 0
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                file_size = int(resp.headers.get("Content-Length", 0))
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(10_000_000):
                        await f.write(chunk)
                        break   # only first 10 MB is enough for MediaInfo
        stdout = await _run_mediainfo(dest)
        tc     = f"<h4>📌 {os.path.basename(dest)}</h4><br><br>"
        if stdout:
            tc += _parse_mediainfo(stdout, file_size)
        page   = await telegraph.create_page("MediaInfo — NXT HUB", tc)
        await msg.edit_text(
            f"📊 <b>MediaInfo</b>\n\n"
            f"➤ <b>File :</b> <code>{filename}</code>\n"
            f"➤ <b>Link :</b> https://graph.org/{page['path']}",
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=False,
        )
    except Exception as e:
        LOGGER.error(f"[mediainfo] URL error: {e}")
        await msg.edit_text(f"❌ Failed: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except Exception:
            pass


async def _gen_mediainfo_from_media(media, reply_msg: Message, msg: Message):
    dest = os.path.join(_MEDIAINFO_DIR, getattr(media, "file_name", "mediainfo_file"))
    try:
        if getattr(media, "file_size", 0) <= 50_000_000:
            await reply_msg.download(dest)
        else:
            async with aiofiles.open(dest, "ab") as f:
                async for chunk in reply_msg._client.stream_media(media, limit=5):
                    await f.write(chunk)
        file_size = getattr(media, "file_size", 0)
        stdout    = await _run_mediainfo(dest)
        tc        = f"<h4>📌 {os.path.basename(dest)}</h4><br><br>"
        if stdout:
            tc += _parse_mediainfo(stdout, file_size)
        page = await telegraph.create_page("MediaInfo — NXT HUB", tc)
        await msg.edit_text(
            f"📊 <b>MediaInfo</b>\n\n"
            f"➤ <b>File :</b> <code>{os.path.basename(dest)}</code>\n"
            f"➤ <b>Link :</b> https://graph.org/{page['path']}",
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=False,
        )
    except Exception as e:
        LOGGER.error(f"[mediainfo] Media error: {e}")
        await msg.edit_text(f"❌ Failed: <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except Exception:
            pass


@Client.on_message(
    filters.command(["mediainfo", "mi"]) & (filters.private | filters.group)
)
async def cmd_mediainfo(client: Client, message: Message):
    if not await auth_required(message):
        return

    await _ensure_dir()
    await telegraph.create_account()

    help_text = (
        "📊 <b>MediaInfo Usage</b>\n\n"
        "• Reply to a video/audio/document: <code>/mi</code>\n"
        "• Provide a direct URL: <code>/mi &lt;url&gt;</code>"
    )

    rply = message.reply_to_message
    args = message.text.split(maxsplit=1)

    # URL mode
    if len(args) > 1:
        url = args[1].strip()
        msg = await message.reply_text("⏳ <i>Generating MediaInfo…</i>", parse_mode=enums.ParseMode.HTML)
        return await _gen_mediainfo_from_url(url, msg)

    # Reply-to-text mode (URL in reply)
    if rply and rply.text:
        msg = await message.reply_text("⏳ <i>Generating MediaInfo…</i>", parse_mode=enums.ParseMode.HTML)
        return await _gen_mediainfo_from_url(rply.text.strip(), msg)

    # Reply-to-media mode
    if rply:
        media = next(
            (getattr(rply, t, None) for t in ["document", "video", "audio", "voice", "animation", "video_note"]
             if getattr(rply, t, None)),
            None,
        )
        if media:
            msg = await message.reply_text("⏳ <i>Generating MediaInfo…</i>", parse_mode=enums.ParseMode.HTML)
            return await _gen_mediainfo_from_media(media, rply, msg)

    await message.reply_text(help_text, parse_mode=enums.ParseMode.HTML)
