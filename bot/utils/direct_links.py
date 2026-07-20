"""
URL router — resolves any input to a download backend.

Backends:
  aria2    → magnet / .torrent  → aria2c
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

# M3U8/HLS patterns (URL-based)
M3U8_RE = re.compile(
    r"\.m3u8(\?|#|$)"
    r"|/hls/"
    r"|/m3u8/"
    r"|playlist\.m3u8"
    r"|index\.m3u8"
    r"|chunklist\.m3u8"
    r"|master\.m3u8"
    r"|[?&]format=(hls|m3u8)"
    r"|[?&]type=(hls|m3u8)",
    re.I,
)

# M3U8/HLS Content-Type values returned by servers
_M3U8_CT = re.compile(
    r"application/(vnd\.apple\.mpegurl|x-mpegurl|mpegurl)"
    r"|audio/mpegurl"
    r"|video/mp2t",
    re.I,
)

# Any URL whose path ends in one of these is unambiguously a direct file —
# route straight to the HTTP/aria2 downloader and never bother probing it.
# This is what makes /d work for "all types of direct download links"
# regardless of what a CDN's HEAD response looks like (many WAFs/CDNs,
# including Cloudflare R2 public buckets, block or challenge HEAD requests
# and return a text/html error page for a perfectly normal file).
DIRECT_EXTS = {
    # video
    "mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "ts", "m4v",
    "3gp", "3g2", "mpg", "mpeg", "ogv", "vob", "mts", "m2ts", "f4v",
    # audio
    "mp3", "m4a", "flac", "wav", "aac", "ogg", "wma", "opus", "alac", "m4b",
    # archives
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "iso",
    # documents / ebooks
    "pdf", "epub", "mobi", "azw3", "azw", "docx", "doc", "pptx", "ppt",
    "xlsx", "xls", "txt", "csv", "srt", "ass", "vtt", "sub",
    # images
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg",
    # disk/exe/misc binaries
    "exe", "msi", "dmg", "apk", "deb", "rpm", "bin", "img",
}


def _has_direct_ext(url: str) -> bool:
    path = urlparse(url).path.lower()
    last = path.rsplit("/", 1)[-1]
    ext  = last.rsplit(".", 1)[-1] if "." in last else ""
    return ext in DIRECT_EXTS


async def resolve(url: str) -> dict:
    url = url.strip()

    # ── Magnet / torrent → aria2c ─────────────────────────────
    if url.lower().startswith("magnet:?"):
        return _r(url, torrent=True)
    if urlparse(url).path.lower().endswith(".torrent"):
        return _r(url, torrent=True)

    # ── Telegram link ─────────────────────────────────────────
    if re.search(r"https?://t\.me/", url, re.I):
        return _r(url, tg=True)

    # ── Google Drive (file or folder) ─────────────────────────
    if re.search(r"drive\.google\.com|drive\.usercontent\.google\.com", url, re.I):
        return _r(url, gdrive=True)

    # ── M3U8 / HLS ────────────────────────────────────────────
    if M3U8_RE.search(url):
        return _r(url, ytdlp=True)

    # ── Known video platforms ─────────────────────────────────
    if YTDLP_RE.search(url):
        return _r(url, ytdlp=True)

    # ── Direct file link (known extension) → straight to HTTP ─
    # Skip all probing entirely. This is the important fix: a link ending
    # in .mkv/.mp4/.zip/etc IS the file — trusting a HEAD probe's
    # Content-Type here is what misroutes CDN/WAF-protected direct links
    # (e.g. Cloudflare R2 buckets that return a text/html block page for
    # HEAD requests) into yt-dlp, which then fails since it's not a real
    # webpage.
    if _has_direct_ext(url):
        return _r(url)

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

    # ── Unknown URL — sniff Content-Type ──────────────────────
    # Try HEAD first (cheap), but don't trust it blindly: lots of CDNs and
    # WAFs mishandle HEAD (403/503 + text/html challenge page) even though
    # a real GET for the file works fine. If HEAD comes back non-2xx or the
    # server disallows it, fall back to a tiny ranged GET before deciding.
    ct, fu, ok = await _probe(url, "head")
    if not ok:
        ct, fu, ok = await _probe(url, "get")

    if not ok:
        # Couldn't get any clean signal at all — don't guess ytdlp, just
        # hand it to the HTTP downloader; if the URL truly isn't a file,
        # that will fail with a clear "not a file" error instead of a
        # confusing yt-dlp "unsupported URL" error.
        return _r(url)

    if _M3U8_CT.search(ct):
        return _r(fu, ytdlp=True)
    if M3U8_RE.search(fu):
        return _r(fu, ytdlp=True)
    if _has_direct_ext(fu):
        return _r(fu)
    if "text/html" in ct:
        return _r(fu, ytdlp=True)
    return _r(fu)


async def _probe(url: str, method: str) -> tuple[str, str, bool]:
    """Returns (content_type, final_url, ok). ok is False on network error,
    non-2xx status, or a disallowed method — signalling the caller should
    not trust `ct` and should try another probe or just fall back to HTTP."""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": UA}
            if method == "get":
                headers["Range"] = "bytes=0-1023"
            req = session.head if method == "head" else session.get
            async with req(
                url, headers=headers,
                allow_redirects=True, timeout=TIMEOUT,
            ) as resp:
                fu = str(resp.url)
                if resp.status >= 400:
                    return "", fu, False
                ct = resp.headers.get("Content-Type", "").lower()
                return ct, fu, True
    except Exception:
        return "", url, False


def _r(url, ytdlp=False, torrent=False, tg=False, jdleech=False, gdrive=False) -> dict:
    return {
        "url":        url,
        "use_ytdlp":  ytdlp,
        "is_torrent": torrent,
        "is_magnet":  url.lower().startswith("magnet:") if torrent else False,
        "is_tg":      tg,
        "is_mega":    False,
        "is_jdleech": jdleech,
        "is_gdrive":  gdrive,
    }
