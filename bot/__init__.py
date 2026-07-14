import platform
import psutil
import time
import logging
from asyncio import Lock as AsyncLock
from pyrogram import Client, enums
import config

_start_time = time.time()

# Setup logger
LOGGER = logging.getLogger("NXTL_BOT")
LOGGER.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
LOGGER.addHandler(handler)

# Task tracking
task_dict = {}
task_dict_lock = AsyncLock()

# Build session list - bot always present; user session if provided
app = Client(
    "nxthub_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    plugins={"root": "bot/handlers"},
    sleep_threshold=10,
    max_concurrent_transmissions=16,
)

# User client for 4 GB uploads (optional)
user_app: Client | None = None
if config.SESSION:
    user_app = Client(
        "nxthub_user",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=config.SESSION,
        max_concurrent_transmissions=16,
    )


def uploader_client() -> Client:
    """Return user_app if available (4 GB), else bot app (2 GB)."""
    return user_app if user_app else app


async def send_startup_log() -> None:
    if not config.LOG_CHANNEL:
        return
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu  = psutil.cpu_count()
    text = (
        "<b>🟢 NXT_HUB Bot Started</b>\n"
        f"🖥 OS: {platform.system()} {platform.release()}\n"
        f"🐍 Python: {platform.python_version()}\n"
        f"🧠 RAM: {ram.used // 1024**2} MB / {ram.total // 1024**2} MB\n"
        f"💾 Disk: {disk.used // 1024**3} GB / {disk.total // 1024**3} GB free\n"
        f"💻 CPUs: {cpu}"
    )
    try:
        await app.send_message(config.LOG_CHANNEL, text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


async def log_leech(username: str, uid: int, filename: str, task_id: str, elapsed: float) -> None:
    if not config.LOG_CHANNEL:
        return
    text = (
        "<b>📥 Leech Completed</b>\n"
        f"👤 {username} (<code>{uid}</code>)\n"
        f"📄 <code>{filename}</code>\n"
        f"🆔 Task: <code>{task_id}</code>\n"
        f"⏱ {elapsed:.1f}s"
    )
    try:
        await app.send_message(config.LOG_CHANNEL, text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass
