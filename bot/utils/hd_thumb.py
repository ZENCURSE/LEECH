"""
HD Thumbnail Generator — NXTL
Generates a proper 1280×720 JPEG thumbnail for any file.

Priority chain:
  1. User's custom thumbnail (settings)
  2. TMDB backdrop (API key required)
  3. Fanart.tv HD art (API key required)
  4. ffmpeg frame extract at 30% of video duration
  5. Guaranteed title-card fallback (always succeeds)

Output is always:
  - JPEG format
  - 1280×720, letterboxed with black bars if needed
  - ≤ 200 KB (Telegram's reliable thumb size limit)
"""
import os
import re
import time
import asyncio
import aiohttp
import aiofiles
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE

import config
from bot import LOGGER

_UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131"
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_W, _H   = 1280, 720
_MAX_KB  = 200          # Telegram reliable limit via MTProto


# ════════════════════════════════════════════════════════════
#  Core: prepare a proper JPEG within size limit
# ════════════════════════════════════════════════════════════

def prep_thumb(src: str, out: str | None = None) -> str | None:
    """
    Convert any image to a 1280×720 letterboxed JPEG ≤ 200 KB.
    Progressively lowers quality until size target is met.
    Returns output path, or None on failure.
    """
    if not src or not os.path.exists(src):
        return None
    try:
        from PIL import Image
        with Image.open(src) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size

            # Scale to fit 1280×720, keeping aspect ratio
            scale = min(_W / w, _H / h)
            nw, nh = int(w * scale), int(h * scale)
            rgb = rgb.resize((nw, nh), Image.LANCZOS)

            # Paste onto a black 1280×720 canvas (letterbox)
            canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
            canvas.paste(rgb, ((_W - nw) // 2, (_H - nh) // 2))

        out = out or (src.rsplit(".", 1)[0] + "_hd.jpg")

        # Progressive compression until ≤ MAX_KB
        for quality in (95, 88, 80, 70, 60, 50):
            canvas.save(out, "JPEG", quality=quality,
                        subsampling=0, optimize=True)
            if os.path.getsize(out) <= _MAX_KB * 1024:
                break

        return out if os.path.exists(out) else None

    except Exception as e:
        LOGGER.warning(f"[HDThumb] prep_thumb failed: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  Online sources
# ════════════════════════════════════════════════════════════

def _guess_title(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r'[\._\-]', ' ', name)
    name = re.sub(
        r'\b(1080p|720p|480p|4K|2160p|BluRay|BDRip|WEB.?DL|WEBRip|HDTV'
        r'|x264|x265|HEVC|AAC|DD5\.1|Dual|Audio|Hindi|Tamil|Telugu|English'
        r'|Multi|S\d{2}E\d{2}|S\d{2}|E\d{2})\b.*',
        '', name, flags=re.I,
    ).strip()
    return name or "Untitled"


async def _fetch_bytes(url: str, session: aiohttp.ClientSession) -> bytes | None:
    try:
        async with session.get(
            url, headers={"User-Agent": _UA}, timeout=_TIMEOUT
        ) as r:
            if r.status == 200:
                return await r.read()
    except Exception:
        pass
    return None


async def _tmdb(title: str, session: aiohttp.ClientSession, tmp: str) -> str | None:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key:
        return None
    try:
        import urllib.parse
        q = urllib.parse.quote(title)
        async with session.get(
            f"https://api.themoviedb.org/3/search/multi?api_key={key}&query={q}",
            timeout=_TIMEOUT,
        ) as r:
            data = await r.json()

        results = data.get("results") or []
        if not results:
            return None

        item    = results[0]
        media   = item.get("media_type", "movie")
        item_id = item.get("id")

        async with session.get(
            f"https://api.themoviedb.org/3/{media}/{item_id}/images"
            f"?api_key={key}&include_image_language=en,null",
            timeout=_TIMEOUT,
        ) as r:
            imgs = await r.json()

        backdrop = ((imgs.get("backdrops") or [{}])[0]).get("file_path")
        if not backdrop:
            return None

        raw = await _fetch_bytes(
            f"https://image.tmdb.org/t/p/w1280{backdrop}", session
        )
        if not raw:
            return None

        dest = os.path.join(tmp, f"tmdb_{int(time.time())}.jpg")
        async with aiofiles.open(dest, "wb") as f:
            await f.write(raw)
        return dest

    except Exception as e:
        LOGGER.debug(f"[HDThumb] TMDB: {e}")
        return None


async def _fanart(title: str, session: aiohttp.ClientSession, tmp: str) -> str | None:
    fa_key   = getattr(config, "FANART_API_KEY", "").strip()
    tmdb_key = getattr(config, "TMDB_API_KEY", "").strip()
    if not fa_key or not tmdb_key:
        return None
    try:
        import urllib.parse
        q = urllib.parse.quote(title)
        async with session.get(
            f"https://api.themoviedb.org/3/search/movie?api_key={tmdb_key}&query={q}",
            timeout=_TIMEOUT,
        ) as r:
            d = await r.json()

        item_id = ((d.get("results") or [{}])[0]).get("id")
        if not item_id:
            return None

        async with session.get(
            f"https://webservice.fanart.tv/v3/movies/{item_id}?api_key={fa_key}",
            timeout=_TIMEOUT,
        ) as r:
            fa = await r.json()

        arts = fa.get("moviebackground") or fa.get("moviethumb") or []
        if not arts:
            return None

        raw = await _fetch_bytes(arts[0]["url"], session)
        if not raw:
            return None

        dest = os.path.join(tmp, f"fanart_{int(time.time())}.jpg")
        async with aiofiles.open(dest, "wb") as f:
            await f.write(raw)
        return dest

    except Exception as e:
        LOGGER.debug(f"[HDThumb] Fanart: {e}")
        return None


async def _ffmpeg_frame(video: str, tmp: str) -> str | None:
    """Extract a frame at 30% of video duration using ffmpeg."""
    try:
        # Get duration
        proc = await create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video, stdout=PIPE, stderr=PIPE,
        )
        out, _ = await proc.communicate()
        duration = float((out.decode().strip()) or "0")
    except Exception:
        duration = 0

    seek = max(duration * 0.30, 3.0) if duration > 10 else 1.0
    dest = os.path.join(tmp, f"frame_{int(time.time())}.jpg")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek), "-i", video,
        "-vframes", "1",
        "-vf", f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
               f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2:black",
        "-q:v", "2", "-y", dest,
    ]
    try:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, err = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 1024:
            return dest
        LOGGER.debug(f"[HDThumb] ffmpeg frame: {err.decode()[:120]}")
    except Exception as e:
        LOGGER.debug(f"[HDThumb] ffmpeg error: {e}")
    return None


async def _title_card(title: str, tmp: str) -> str | None:
    """Always-available fallback: dark gradient card with title text."""
    safe  = title.replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")[:55]
    dest  = os.path.join(tmp, f"card_{int(time.time())}.jpg")
    vf    = (
        f"drawtext=text='{safe}':fontsize=70:fontcolor=white"
        f":x=(w-text_w)/2:y=(h-text_h)/2"
        f":shadowcolor=black@0.8:shadowx=4:shadowy=4"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=0x141824:size={_W}x{_H}:rate=1",
        "-vframes", "1", "-vf", vf, "-y", dest,
    ]
    try:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        await proc.communicate()
        if proc.returncode == 0 and os.path.exists(dest):
            return dest
    except Exception as e:
        LOGGER.debug(f"[HDThumb] title card error: {e}")
    return None


# ════════════════════════════════════════════════════════════
#  Public API
# ════════════════════════════════════════════════════════════

async def generate_hd_thumb(
    file_path: str,
    uid: int = 0,
    custom_thumb: str | None = None,
) -> str | None:
    """
    Generate the best possible HD thumbnail for file_path.
    Always returns a path (title card guaranteed), or None only on
    total ffmpeg failure.

    Args:
        file_path    : path to the file being uploaded
        uid          : Telegram user ID (to check custom thumb in settings)
        custom_thumb : explicit override thumb path
    """
    tmp = os.path.join(config.DOWNLOAD_DIR, ".thumbs")
    os.makedirs(tmp, exist_ok=True)

    # ── 0. Explicit override ──────────────────────────────────
    if custom_thumb and os.path.exists(custom_thumb):
        LOGGER.info("[HDThumb] using explicit override")
        return prep_thumb(custom_thumb, os.path.join(tmp, f"override_{int(time.time())}.jpg"))

    # ── 1. User's saved custom thumb ─────────────────────────
    if uid:
        try:
            from bot.database import users_db
            s = users_db.get_settings(uid)
            tp = s.get("thumb_path")
            if tp and os.path.exists(tp):
                LOGGER.info("[HDThumb] using user custom thumb")
                return prep_thumb(tp, os.path.join(tmp, f"usr_{uid}_{int(time.time())}.jpg"))
        except Exception:
            pass

    title = _guess_title(file_path)
    LOGGER.info(f"[HDThumb] generating for '{title}'")

    # ── 2. TMDB + 3. Fanart (online, needs API keys) ─────────
    async with aiohttp.ClientSession() as session:
        for attempt_fn, label in [(_tmdb, "TMDB"), (_fanart, "Fanart")]:
            raw_path = await attempt_fn(title, session, tmp)
            if raw_path:
                result = prep_thumb(raw_path, raw_path.replace(".jpg", "_hd.jpg"))
                if result:
                    LOGGER.info(f"[HDThumb] ✅ {label}")
                    return result

    # ── 4. ffmpeg frame (works for any video file) ───────────
    is_video = file_path.lower().endswith((
        ".mp4", ".mkv", ".avi", ".mov", ".webm",
        ".ts", ".m2ts", ".flv", ".wmv", ".mpg", ".mpeg",
    ))
    if is_video and os.path.exists(file_path):
        raw_path = await _ffmpeg_frame(file_path, tmp)
        if raw_path:
            result = prep_thumb(raw_path, raw_path.replace(".jpg", "_hd.jpg"))
            if result:
                LOGGER.info("[HDThumb] ✅ ffmpeg frame")
                return result

    # ── 5. Title card (guaranteed fallback) ──────────────────
    raw_path = await _title_card(title, tmp)
    if raw_path:
        result = prep_thumb(raw_path, raw_path.replace(".jpg", "_hd.jpg"))
        if result:
            LOGGER.info("[HDThumb] ✅ title card fallback")
            return result

    LOGGER.error("[HDThumb] all methods failed")
    return None
