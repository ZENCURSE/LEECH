"""
URL router — resolves any input to a download backend.

Backends:
  qbt      → magnet / .torrent  → qBittorrent
  ytdlp    → video platforms, M3U8/HLS
  jdleech  → premium hosters (explicit /jdleech command)
  ddl      → known hosting sites → direct link extractor → HTTP
  http     → plain file URLs
  tg       → Telegram message links
"""
import re
import asyncio
import aiohttp
from urllib.parse import urlparse

UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)

# Native video/audio platforms — use yt-dlp
YTDLP_RE = re.compile(
    r"(youtube\.com|youtu\.be"
    r"|dailymotion\.com|vimeo\.com|twitch\.tv"
    r"|instagram\.com|twitter\.com|x\.com"
    r"|facebook\.com|tiktok\.com|reddit\.com"
    r"|streamable\.com|mixdrop\.|vidoza\.|voe\.sx"
    r"|upstream\.|fembed\.|streamtape\.|doodstream\."
    r"|filelions\.|streamwish\.|clicknupload\.|streamlare\."
    r"|vupload\.|mp4upload\.|vidplay\.|filemoon\.)",
    re.I,
)

# M3U8/HLS patterns
M3U8_RE = re.compile(
    r"\.m3u8(\?|$)|/hls/|/m3u8/|playlist\.m3u8|index\.m3u8|chunklist\.m3u8|master\.m3u8",
    re.I,
)


async def resolve(url: str) -> dict:
    url = url.strip()

    # ── Magnet / torrent → qBittorrent ───────────────────────
    if url.lower().startswith("magnet:?"):
        return _r(url, torrent=True)
    if urlparse(url).path.lower().endswith(".torrent"):
        return _r(url, torrent=True)

    # ── Telegram link ─────────────────────────────────────────
    if re.search(r"https?://t\.me/", url, re.I):
        return _r(url, tg=True)

    # ── M3U8 / HLS ────────────────────────────────────────────
    if M3U8_RE.search(url):
        return _r(url, ytdlp=True)

    # ── Known video platforms ─────────────────────────────────
    if YTDLP_RE.search(url):
        return _r(url, ytdlp=True)

    # ── Known DDL hosters → resolve to direct URL ─────────────
    try:
        from bot.utils.direct_link_generator import generate_direct_link, is_supported
        if is_supported(url):
            loop   = asyncio.get_event_loop()
            direct = await loop.run_in_executor(None, generate_direct_link, url)
            if direct and isinstance(direct, str) and direct != url:
                if YTDLP_RE.search(direct):
                    return _r(direct, ytdlp=True)
                return _r(direct)   # plain HTTP download
    except Exception:
        pass

    # ── Unknown URL — sniff Content-Type ─────────────────────
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                url, headers={"User-Agent": UA},
                allow_redirects=True, timeout=TIMEOUT,
            ) as resp:
                ct = resp.headers.get("Content-Type", "").lower()
                fu = str(resp.url)
                if "text/html" in ct:
                    return _r(fu, ytdlp=True)
                return _r(fu)
    except Exception:
        return _r(url)   # fallback: try HTTP download


def _r(url, ytdlp=False, torrent=False, tg=False, jdleech=False) -> dict:
    return {
        "url":        url,
        "use_ytdlp":  ytdlp,
        "is_torrent": torrent,
        "is_magnet":  url.lower().startswith("magnet:") if torrent else False,
        "is_tg":      tg,
        "is_mega":    False,
        "is_jdleech": jdleech,
    }
