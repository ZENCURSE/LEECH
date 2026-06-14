"""
/speedtest — run a network speed test.
Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML).
"""
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from bot import LOGGER
from bot.handlers._auth import auth_required
from bot.utils.size_utils import human_size


@Client.on_message(
    filters.command(["speedtest", "speed"]) & (filters.private | filters.group)
)
async def cmd_speedtest(client: Client, message: Message):
    if not await auth_required(message):
        return

    msg = await message.reply_text(
        "⚡ <i>Running speedtest… this takes ~30s</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        from speedtest import Speedtest, ConfigRetrievalError
        import asyncio

        def _run_test():
            st = Speedtest()
            st.get_best_server()
            st.download()
            st.upload()
            st.results.share()
            return st.results.dict()

        result = await asyncio.get_event_loop().run_in_executor(None, _run_test)

        text = (
            "╔═「 ⚡ <b>SPEEDTEST</b> 」\n"
            "║\n"
            "╠═「 🌐 <b>NETWORK</b> 」\n"
            f"║  ➤ <b>Download</b>  :  {human_size(result['download'] / 8)}/s\n"
            f"║  ➤ <b>Upload</b>    :  {human_size(result['upload'] / 8)}/s\n"
            f"║  ➤ <b>Ping</b>      :  {result['ping']:.1f} ms\n"
            "║\n"
            "╠═「 🖥 <b>SERVER</b> 」\n"
            f"║  ➤ <b>Name</b>     :  {result['server']['name']}\n"
            f"║  ➤ <b>Country</b>  :  {result['server']['country']} ({result['server']['cc']})\n"
            f"║  ➤ <b>Sponsor</b>  :  {result['server']['sponsor']}\n"
            f"║  ➤ <b>Latency</b>  :  {result['server']['latency']}\n"
            "║\n"
            "╠═「 📡 <b>CLIENT</b> 」\n"
            f"║  ➤ <b>IP</b>       :  {result['client']['ip']}\n"
            f"║  ➤ <b>ISP</b>      :  {result['client']['isp']}\n"
            f"║  ➤ <b>Country</b>  :  {result['client']['country']}\n"
            "╚══════════════════════"
        )

        photo = result.get("share")
        if photo:
            try:
                await msg.delete()
                await message.reply_photo(photo, caption=text, parse_mode=enums.ParseMode.HTML)
                return
            except Exception:
                pass
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)

    except Exception as e:
        LOGGER.error(f"[speedtest] {e}")
        await msg.edit_text(
            f"❌ Speedtest failed: <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
