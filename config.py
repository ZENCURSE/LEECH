# ═══════════════════════════════════════════════════════════════
#  NXT_HUB Leech Bot — Configuration
#  Edit values below directly. No .env file needed.
# ═══════════════════════════════════════════════════════════════

# ── Telegram ──────────────────────────────────────────────────
API_ID           = 22574649
API_HASH         = "e3730dd3cbdf1ac2c80e7b6ed6d06f13"
BOT_TOKEN        = "8539709524:AAE-IvLSV5pxVO1n91N7Nr2mAv4y1dFoPbI"
SESSION          = ""           # Pyrogram session string — leave blank for 2 GB limit
                                # Fill with Premium account session for 4 GB uploads
OWNER_ID         = 5417874390
LOG_CHANNEL      = -1002329590802
AUTHORIZED_CHATS = [-1003882018027,-1003876799341]           # e.g. [123456789, -1001234567890]


# ── MongoDB ───────────────────────────────────────────────────
# Paste your MongoDB Atlas URI here. Leave blank for flat-file fallback.
# Example: "mongodb+srv://user:pass@cluster0.abcde.mongodb.net/?retryWrites=true"
MONGO_URI = "mongodb+srv://pankajameher2:pankajameher2@cluster0.zoczhsw.mongodb.net/?appName=Cluster0"
MONGO_DB  = "nxthub"

# ── JDownloader (MyJDownloader.org) ──────────────────────────────────
JD_EMAIL    = ""   # your MyJDownloader account email
JD_PASS     = ""   # your MyJDownloader account password

# --------- Mega ------------------------
MEGA_EMAIL = "vikekil325@aspensif.com"
MEGA_PASSWORD = "Rajangarg@234"

# ── Thumbnail APIs ────────────────────────────────────────────
TMDB_API_KEY   = "135789526229bd260ddd6149f5489dda"
OMDB_API_KEY   = "8285a8f6"
FANART_API_KEY = "23b1406ab6b415e681ac750e42bd2b1e"

# ── Download ──────────────────────────────────────────────────
DOWNLOAD_DIR = "/downloads"
MAX_TASKS    = 4               # Concurrent tasks per user
TOTAL_TASKS = 12  
PROGRESS_UPDATE_SEC = 7

# ── Upload ────────────────────────────────────────────────────
# 2147483648 = 2 GB  (normal account)
# 4294967296 = 4 GB  (Telegram Premium SESSION required)
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024
AS_DOCUMENT     = False        # True = always upload as document

# ── Auto-rename ───────────────────────────────────────────────
STRIP_CHARS = r"[.\-_()[\]{}]+"

# ── Aria2 RPC ─────────────────────────────────────────────────
ARIA2_HOST   = "http://localhost"
ARIA2_PORT   = 6800
ARIA2_SECRET = "nxt_hub"              # Leave blank or set a secret token

# ── Branding ──────────────────────────────────────────────────
WATERMARK = "@NXT_HUB"

# ── Encoding (FFmpeg) ─────────────────────────────────────────
ENCODE_DIR      = "/downloads_enc"   # Where encoded files are written
WATERMARK_FILE  = "bot/encoding/extras/watermark.ass"  # ASS watermark overlay

# ── Dump Channel ───────────────────────────────────────────────
# Set this to a channel/group ID (negative int) to have the bot
# automatically forward every leeched file to the channel.
# The bot must be an admin with "Post Messages" permission there.
# Leave 0 to disable the dump channel feature.
DUMP_CHANNEL     = -1004429190957             # e.g. -1001234567890
DUMP_CHANNEL_TAG = True          # Whether to tag the uploader's username in dump

# ── Web Selection UI ──────────────────────────────────────────
# Put your VPS IP below. That's all you need to change.
# Example: BASE_URL = "http://123.456.789.0:8080"
BASE_URL     = "http://143.198.222.137"          # ← PASTE YOUR VPS IP HERE
WEB_PORT     = 8081        # Port for the FastAPI web server

# ── qBittorrent ────────────────────────────────────────────────
QBT_HOST     = "localhost"
QBT_PORT     = 8090
QBT_USERNAME = "admin"
QBT_PASSWORD = "adminadmin"
