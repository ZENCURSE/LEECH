"""
NXT_HUB Leech Bot — Entry Point
Integrated with ENCODING-BOT for FFmpeg encode support.
"""
import os
import asyncio
import subprocess
import uvloop
import config
from health_check import start_health_server

os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
os.makedirs(config.DOWNLOAD_DIR + "_enc", exist_ok=True)
os.makedirs("data", exist_ok=True)
os.makedirs("data/thumbs", exist_ok=True)
os.makedirs("data/cookies", exist_ok=True)


def _start_web_server():
    """Start the FastAPI file-selection web server in a background thread."""
    if not getattr(config, "BASE_URL", "").strip():
        return   # BASE_URL not set — web selection disabled
    import threading
    from web.app import run_web_server
    t = threading.Thread(target=run_web_server, daemon=True, name="web-selector")
    t.start()
    print(f"[web] File selector running on port {getattr(config, 'WEB_PORT', 8080)}")


def _start_aria2():
    """aria2 is used for HTTP direct downloads only. Torrents handled by qBittorrent."""
    cmd = [
        "aria2c",
        "--enable-rpc",
        f"--rpc-listen-port={config.ARIA2_PORT}",
        f"--rpc-secret={config.ARIA2_SECRET}",
        "--rpc-listen-all=false",
        "--daemon=true",
        "--log-level=warn",
        "--max-concurrent-downloads=10",
        "--split=16",
        "--max-connection-per-server=16",
        "--min-split-size=5M",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        f"--dir={config.DOWNLOAD_DIR}",
    ]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[aria2] Started (HTTP downloads only).")
    except FileNotFoundError:
        print("[aria2] WARNING: aria2c not found.")


async def main():
    _start_aria2()
    _start_web_server()
    await asyncio.sleep(1)

    from bot import app, user_app, send_startup_log
    from bot.database.users_db import init_db
    await init_db()

    await start_health_server()

    # Boot JDownloader if credentials provided
    jd_email = getattr(config, "JD_EMAIL", "").strip()
    jd_pass  = getattr(config, "JD_PASS",  "").strip()
    if jd_email and jd_pass:
        from bot.core.jdownloader_booter import jdownloader
        asyncio.create_task(jdownloader.boot())
        print("[JD] Booting JDownloader…")
    else:
        print("[JD] JD_EMAIL/JD_PASS not set — JDownloader disabled.")

    async with app:
        if user_app:
            await user_app.start()
            print("[user] Premium session active — 4 GB upload limit.")

        await send_startup_log()

        # ── Bot commands menu ──────────────────────────────
        from pyrogram.types import BotCommand
        await app.set_bot_commands([
            BotCommand("start",    "👋 Start the bot"),
            BotCommand("d",        "⬇️ Download / leech a URL"),
            BotCommand("jdleech",  "🔗 JD-style multi-host download"),
            BotCommand("status",   "📊 View your active tasks"),
            BotCommand("cancel",   "🚫 Cancel a task"),
            BotCommand("encode",   "🎬 Encode a video (reply to file)"),
            BotCommand("encurl",   "🎬 Download & encode a URL"),
            BotCommand("encsub",   "📄 Encode video + external subtitle (hardsub)"),
            BotCommand("encset",   "⚙️ Configure encoding settings"),
            BotCommand("vset",     "👁 View current encode settings"),
            BotCommand("settings", "⚙️ Personal leech settings"),
            BotCommand("help",     "📖 Full command reference"),
            BotCommand("about",    "ℹ️ About this bot"),
            BotCommand("mi",       "📊 MediaInfo for a file or URL"),
            BotCommand("speedtest","⚡ Network speed test"),
            BotCommand("shell",    "🖥 Run shell command (owners only)"),
        ])

        print(f"[bot] @{(await app.get_me()).username} is running.")
        await asyncio.Event().wait()

        if user_app:
            await user_app.stop()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
