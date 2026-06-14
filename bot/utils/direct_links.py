"""
Resolve any URL to a downloadable form.

Strategy:
  1. Magnet          → aria2
  2. Mega.nz         → mega_download
  3. Telegram link   → tg_downloader
  4. Known DDL host  → generate_direct_link() → HTTP download
  5. Known yt-dlp site / M3U8 → yt-dlp
  6. Unknown URL     → HEAD sniff:
       HTML          → try yt-dlp
       binary/media  → direct HTTP download
"""
import re
import asyncio
import aiohttp
from urllib.parse import urlparse

UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)

# Sites yt-dlp handles natively (video platforms)
YTDLP_DOMAINS = re.compile(
    r"(youtube\.com|youtu\.be"
    r"|dailymotion\.com|vimeo\.com|twitch\.tv"
    r"|instagram\.com|twitter\.com|x\.com"
    r"|facebook\.com|tiktok\.com|reddit\.com"
    r"|streamable\.com|mixdrop\.|vidoza\."
    r"|voe\.sx|upstream\.|fembed\.|jwplayer\.)",
    re.I,
)
# Hosting sites that yt-dlp can also rip (fallback after DDL extractor)
YTDLP_HOST_DOMAINS = re.compile(
    r"(streamtape\.|doodstream\.|filelions\.|clicknupload\."
    r"|streamlare\.|vupload\.)",
    re.I,
)
M3U8_RE = re.compile(r"\.m3u8(\?|$)", re.I)


async def _head(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.head(
            url, headers={"User-Agent": UA},
            allow_redirects=True, timeout=TIMEOUT,
        ) as r:
            ct = r.headers.get("Content-Type", "").lower()
            return {"content_type": ct, "final_url": str(r.url)}
    except Exception:
        return {"content_type": "", "final_url": url}


async def resolve(url: str) -> dict:
    """
    Returns a dict with keys:
      url        - resolved/final URL
      use_ytdlp  - True → pass to ytdlp_download
      is_torrent - True → .torrent URL
      is_magnet  - True → magnet URI
      is_tg      - True → Telegram message link
      is_mega    - True → Mega.nz link
      is_jdleech - True → use JDownloader (manual opt-in only)
    """
    url = url.strip()

    # ── 1. Magnet ────────────────────────────────────────────
    if url.lower().startswith("magnet:?"):
        return _r(url, magnet=True)

    # ── 2. .torrent file URL ──────────────────────────────────
    if urlparse(url).path.lower().endswith(".torrent"):
        return _r(url, torrent=True)

    # ── 3. Telegram message link ──────────────────────────────
    if re.search(r"https?://t\.me/", url, re.I):
        return _r(url, tg=True)

    # ── 4. Mega.nz ───────────────────────────────────────────
    if re.search(r"mega\.nz/|mega\.co\.nz/", url, re.I):
        return _r(url, mega=True)

    # ── 5. M3U8 stream ───────────────────────────────────────
    if M3U8_RE.search(url):
        return _r(url, ytdlp=True)

    # ── 6. Known yt-dlp video platforms ──────────────────────
    if YTDLP_DOMAINS.search(url):
        return _r(url, ytdlp=True)

    # ── 7. Known DDL hosting sites → resolve direct link ─────
    #  generate_direct_link() is sync (uses requests internally), run in executor
    try:
        from bot.utils.direct_link_generator import generate_direct_link, is_supported
        if is_supported(url):
            loop   = asyncio.get_event_loop()
            direct = await loop.run_in_executor(None, generate_direct_link, url)
            if direct and isinstance(direct, str) and direct != url:
                # If the resolved URL is itself a yt-dlp domain, send there
                if YTDLP_DOMAINS.search(direct) or YTDLP_HOST_DOMAINS.search(direct):
                    return _r(direct, ytdlp=True)
                # Otherwise HTTP download the direct link
                return _r(direct)
    except Exception:
        pass  # extractor failed → fall through to yt-dlp / sniff

    # ── 8. Hosting sites yt-dlp can also handle as fallback ──
    if YTDLP_HOST_DOMAINS.search(url):
        return _r(url, ytdlp=True)

    # ── 9. Unknown URL — sniff Content-Type ──────────────────
    async with aiohttp.ClientSession() as session:
        info = await _head(session, url)
        ct   = info["content_type"]
        fu   = info["final_url"]

        if "text/html" in ct:
            # Could be a video page — try yt-dlp
            return _r(fu, ytdlp=True)

        # Binary / media / application → direct download
        return _r(fu)


def _r(url, ytdlp=False, torrent=False, magnet=False,
       tg=False, mega=False, jdleech=False) -> dict:
    return {
        "url":        url,
        "use_ytdlp":  ytdlp,
        "is_torrent": torrent,
        "is_magnet":  magnet,
        "is_tg":      tg,
        "is_mega":    mega,
        "is_jdleech": jdleech,
    }

# Backward-compat alias
get_direct_link = resolve
