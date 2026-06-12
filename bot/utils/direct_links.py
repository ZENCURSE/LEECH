"""
Resolve any URL to a downloadable form.
Strategy:
  1. Magnet / .torrent  → handled by aria2
  2. Known yt-dlp sites → yt-dlp
  3. Known hosting sites → custom extractor → direct link
  4. Unknown URL        → HEAD request to sniff Content-Type
       - text/html      → try yt-dlp (may be a video page)
       - anything else  → treat as direct file download
"""
import re
import asyncio
import asyncio
import aiohttp
from urllib.parse import urlparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)

# Sites that yt-dlp handles natively
YTDLP_DOMAINS = re.compile(
    r"(youtube\.com|youtu\.be|streamtape\.|doodstream\.|filelions\."
    r"|clicknupload\.|streamlare\.|mixdrop\.|vidoza\.|upstream\."
    r"|voe\.sx|vupload\.|fembed\.|jwplayer\.|dailymotion\.com"
    r"|vimeo\.com|twitch\.tv|instagram\.com|twitter\.com|x\.com"
    r"|facebook\.com|tiktok\.com|reddit\.com|streamable\.com)",
    re.I,
)
M3U8_RE = re.compile(r"\.m3u8(\?|$)", re.I)


async def _head(session: aiohttp.ClientSession, url: str) -> dict:
    """Return {'content_type': str, 'final_url': str}"""
    try:
        async with session.head(
            url, headers={"User-Agent": UA}, allow_redirects=True,
            timeout=TIMEOUT,
        ) as r:
            ct = r.headers.get("Content-Type", "").lower()
            return {"content_type": ct, "final_url": str(r.url)}
    except Exception:
        return {"content_type": "", "final_url": url}


async def _get_html(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(
            url, headers={"User-Agent": UA}, allow_redirects=True, timeout=TIMEOUT
        ) as r:
            return await r.text(errors="replace")
    except Exception:
        return ""


# ── Pixeldrain ────────────────────────────────────────────────
def _pixeldrain(url: str) -> str:
    m = re.search(r"pixeldrain\.com/[ul]/([a-zA-Z0-9]+)", url)
    if m:
        return f"https://pixeldrain.com/api/file/{m.group(1)}?download"
    return url


# ── Hubcloud / Hubdrive ───────────────────────────────────────
async def _hubcloud(session: aiohttp.ClientSession, url: str) -> str:
    from bs4 import BeautifulSoup
    html = await _get_html(session, url)
    if not html:
        return url
    soup = BeautifulSoup(html, "lxml")
    # Direct file link in anchor
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if re.search(r"\.(mp4|mkv|avi|mov|zip|rar|7z|pdf|apk|exe|iso)", href, re.I):
            return href
    # Meta refresh redirect
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if meta and meta.get("content"):
        m = re.search(r"url=([^\s;]+)", meta["content"], re.I)
        if m:
            return m.group(1).strip("'\"")
    # JavaScript window.location / href patterns
    m = re.search(r'(?:location\.href|window\.location)\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    return url


# ── GDFlix ────────────────────────────────────────────────────
async def _gdflix(session: aiohttp.ClientSession, url: str) -> str:
    from bs4 import BeautifulSoup
    html = await _get_html(session, url)
    if not html:
        return url
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "drive.google.com" in href or re.search(r"\.(mp4|mkv|zip|rar)", href, re.I):
            return href
    return url


# ── Public resolver ───────────────────────────────────────────
async def resolve(url: str) -> dict:
    """
    Returns:
      url        - final URL to use
      use_ytdlp  - True → pass to ytdlp_download
      is_torrent - True → local .torrent path / remote .torrent URL
      is_magnet  - True → magnet URI
    """
    url = url.strip()

    # Magnet
    if url.lower().startswith("magnet:?"):
        return _r(url, magnet=True)

    # Telegram message link
    if re.search(r"https?://t\.me/", url, re.I):
        return _r(url, tg=True)

    # Mega.nz link
    if re.search(r"mega\.nz/", url, re.I):
        return _r(url, mega=True)

    # Known DDL hosting sites — resolve via JDLeech extractors
    try:
        from bot.utils.direct_link_generator import generate_direct_link, is_supported
        if is_supported(url):
            return _r(url, jdleech=True)
    except Exception:
        pass  # Fall through to yt-dlp

    # .torrent file URL
    parsed_path = urlparse(url).path.lower()
    if parsed_path.endswith(".torrent"):
        return _r(url, torrent=True)

    # M3U8 stream
    if M3U8_RE.search(url):
        return _r(url, ytdlp=True)

    # Known yt-dlp sites
    if YTDLP_DOMAINS.search(url):
        return _r(url, ytdlp=True)

    # Known hosting extractors
    async with aiohttp.ClientSession() as session:
        if "pixeldrain.com" in url:
            return _r(_pixeldrain(url))

        if re.search(r"hubcloud\.|hubdrive\.", url, re.I):
            resolved = await _hubcloud(session, url)
            return _r(resolved)

        if re.search(r"gdflix\.", url, re.I):
            resolved = await _gdflix(session, url)
            return _r(resolved)

        # Unknown URL — sniff content type
        info = await _head(session, url)
        ct   = info["content_type"]
        fu   = info["final_url"]

        # If it's HTML → could be a video page, try yt-dlp
        if "text/html" in ct:
            return _r(fu, ytdlp=True)

        # Binary / octet-stream / video / audio / application → direct download
        return _r(fu)


def _r(url, ytdlp=False, torrent=False, magnet=False, tg=False, mega=False, jdleech=False) -> dict:
    return {"url": url, "use_ytdlp": ytdlp, "is_torrent": torrent, "is_magnet": magnet, "is_tg": tg, "is_mega": mega, "is_jdleech": jdleech}
