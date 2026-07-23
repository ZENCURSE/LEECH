"""
NXT_HUB Leech Bot — Entry Point
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
    """aria2 handles BOTH multi-connection HTTP downloads and torrents/magnets."""
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
        # ── Torrent/magnet peer discovery ────────────────────────
        # Without these, magnets rely ENTIRELY on the trackers listed in
        # the link — if those are slow, rate-limiting, or just down
        # (extremely common for public trackers), the download hangs at
        # 0 peers indefinitely with no error. DHT + PEX give aria2 a way
        # to find peers independent of the trackers actually responding.
        "--enable-dht=true",
        "--dht-listen-port=6881-6999",
        "--bt-enable-lpd=true",           # local peer discovery (same-LAN seedboxes)
        "--enable-peer-exchange=true",    # once we have 1 peer, find more via them
        "--bt-tracker-interval=60",
        "--bt-tracker-timeout=15",
        "--bt-tracker-connect-timeout=15",
        "--bt-request-peer-speed-limit=0",
        "--listen-port=6881-6999",
        "--bt-max-peers=100",
    ]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[aria2] Started (HTTP downloads + torrents/magnets, DHT enabled).")
    except FileNotFoundError:
        print("[aria2] WARNING: aria2c not found.")


async def main():
    _start_aria2()
    _start_web_server()
    await asyncio.sleep(1)

    # ── Health server FIRST — Koyeb requires this to pass before anything else ──
    await start_health_server()

    from bot import app, user_app, send_startup_log
    from bot.database.users_db import init_db
    from bot.utils.thumb_store import cleanup_tmp
    cleanup_tmp()   # wipe leftover temp thumbs from previous run
    # Run DB init in background — health check already passing
    asyncio.create_task(init_db())
    await asyncio.sleep(1)

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
            BotCommand("d",        "⬇️ Download / leech a direct URL"),
            BotCommand("ytdl",     "🎬 Force yt-dlp (YouTube, HLS/M3U8)"),
            BotCommand("torrent",  "🌊 Download a magnet/.torrent link or file"),
            BotCommand("jdleech",  "🔗 JD-style multi-host download"),
            BotCommand("status",   "📊 View your active tasks"),
            BotCommand("cancel",   "🚫 Cancel a task"),
            BotCommand("settings", "⚙️ Personal leech settings"),
            BotCommand("help",     "📖 Full command reference"),
            BotCommand("about",    "ℹ️ About this bot"),
            BotCommand("mi",       "📊 MediaInfo for a file or URL"),
        ])

        print(f"[bot] @{(await app.get_me()).username} is running.")
        await asyncio.Event().wait()

        if user_app:
            await user_app.stop()


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
