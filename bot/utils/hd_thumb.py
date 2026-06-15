"""
HD Thumbnail Generator — NXTL
Priority chain:
  1. TMDB backdrop + title logo composite (1280×720)
  2. Fanart.tv HD art
  3. iTunes poster (letterboxed to 1280×720)
  4. ffmpeg frame extract from the video file (best-quality frame)
  5. Plain coloured title card (guaranteed fallback)
"""
import asyncio
import io
import os
import re
import time
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE

import aiohttp
import aiofiles

import config
from bot import LOGGER

_UA      = "Mozilla/5.0 AppleWebKit/537.36 Chrome/131"
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_THUMB_W = 1280
_THUMB_H = 720


# ════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════

async def _fetch(url: str, session: aiohttp.ClientSession) -> bytes | None:
    try:
        async with session.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT) as r:
            if r.status == 200:
                return await r.read()
    except Exception:
        pass
    return None


def _guess_title(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r'[\._\-]', ' ', name)
    # Strip common release tags
    name = re.sub(
        r'\b(1080p|720p|480p|4K|2160p|BluRay|BDRip|WEB.?DL|WEBRip|HDTV|x264|x265|HEVC|AAC|DD5\.1|Dual|Audio|Hindi|Tamil|Telugu|English|Multi|S\d{2}E\d{2}|S\d{2}|E\d{2})\b.*',
        '', name, flags=re.I
    ).strip()
    return name or "Untitled"


async def _tmdb_thumb(title: str, session: aiohttp.ClientSession) -> bytes | None:
    key = getattr(config, "TMDB_API_KEY", "")
    if not key:
        return None
    try:
        search_url = f"https://api.themoviedb.org/3/search/multi?api_key={key}&query={aiohttp.helpers.requote_uri(title)}&page=1"
        async with session.get(search_url, timeout=_TIMEOUT) as r:
            data = await r.json()
        results = data.get("results", [])
        if not results:
            return None
        item    = results[0]
        media   = item.get("media_type", "movie")
        item_id = item.get("id")

        # Get images
        img_url = f"https://api.themoviedb.org/3/{media}/{item_id}/images?api_key={key}&include_image_language=en,null"
        async with session.get(img_url, timeout=_TIMEOUT) as r:
            imgs = await r.json()

        backdrop = (imgs.get("backdrops") or [{}])[0].get("file_path")
        if not backdrop:
            return None
        return await _fetch(f"https://image.tmdb.org/t/p/w1280{backdrop}", session)
    except Exception as e:
        LOGGER.debug(f"[HDThumb] TMDB failed: {e}")
        return None


async def _fanart_thumb(title: str, session: aiohttp.ClientSession) -> bytes | None:
    key = getattr(config, "FANART_API_KEY", "")
    tmdb_key = getattr(config, "TMDB_API_KEY", "")
    if not key or not tmdb_key:
        return None
    try:
        # Get TMDB ID first
        search = f"https://api.themoviedb.org/3/search/movie?api_key={tmdb_key}&query={aiohttp.helpers.requote_uri(title)}"
        async with session.get(search, timeout=_TIMEOUT) as r:
            d = await r.json()
        item_id = (d.get("results") or [{}])[0].get("id")
        if not item_id:
            return None

        fa_url = f"https://webservice.fanart.tv/v3/movies/{item_id}?api_key={key}"
        async with session.get(fa_url, timeout=_TIMEOUT) as r:
            fa = await r.json()

        arts = fa.get("moviebackground") or fa.get("moviethumb") or []
        if not arts:
            return None
        return await _fetch(arts[0]["url"], session)
    except Exception as e:
        LOGGER.debug(f"[HDThumb] Fanart failed: {e}")
        return None


async def _ffmpeg_thumb(video_path: str, out_path: str) -> bool:
    """Extract best-quality frame using ffmpeg."""
    try:
        # Get video duration first
        dur_proc = await create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
            stdout=PIPE, stderr=PIPE,
        )
        stdout, _ = await dur_proc.communicate()
        duration = float(stdout.decode().strip() or "0")
    except Exception:
        duration = 0

    # Seek to 30% of video for a good frame
    seek = max(duration * 0.3, 5) if duration > 10 else 2

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek),
        "-i", video_path,
        "-vframes", "1",
        "-vf", f"scale={_THUMB_W}:{_THUMB_H}:force_original_aspect_ratio=decrease,pad={_THUMB_W}:{_THUMB_H}:(ow-iw)/2:(oh-ih)/2:black",
        "-q:v", "2",
        "-y", out_path,
    ]
    try:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return True
        LOGGER.debug(f"[HDThumb] ffmpeg failed: {stderr.decode()[:200]}")
    except Exception as e:
        LOGGER.debug(f"[HDThumb] ffmpeg error: {e}")
    return False


async def _title_card(title: str, out_path: str) -> bool:
    """Fallback: generate a coloured title card using ffmpeg drawtext."""
    safe_title = title.replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")[:60]
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=0x1a1a2e:size={_THUMB_W}x{_THUMB_H}:rate=1",
        "-vframes", "1",
        "-vf",
        (
            f"drawtext=text='{safe_title}'"
            f":fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2"
            f":shadowcolor=black:shadowx=3:shadowy=3"
        ),
        "-y", out_path,
    ]
    try:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, _ = await proc.communicate()
        return proc.returncode == 0 and os.path.exists(out_path)
    except Exception:
        return False


# ════════════════════════════════════════════════════════════
#  Public entry point
# ════════════════════════════════════════════════════════════

async def generate_hd_thumb(
    video_path: str,
    uid: int = 0,
    custom_thumb: str | None = None,
) -> str | None:
    """
    Generate the best possible HD thumbnail for video_path.
    Returns path to a JPEG file, or None on total failure.

    Priority:
      1. User's custom thumbnail (if set)
      2. TMDB backdrop
      3. Fanart.tv backdrop
      4. ffmpeg frame extract
      5. Title card
    """
    # ── 0. User custom thumb ──────────────────────────────────
    if custom_thumb and os.path.exists(custom_thumb):
        return custom_thumb

    if uid:
        try:
            from bot.database import users_db
            s = users_db.get_settings(uid)
            tp = s.get("thumb_path")
            if tp and os.path.exists(tp):
                return tp
        except Exception:
            pass

    # ── Prepare output path ───────────────────────────────────
    tmp_dir  = os.path.join(config.DOWNLOAD_DIR, ".thumbs")
    os.makedirs(tmp_dir, exist_ok=True)
    out_path = os.path.join(tmp_dir, f"thumb_{int(time.time())}.jpg")
    title    = _guess_title(video_path)

    LOGGER.info(f"[HDThumb] Generating for '{title}' ({os.path.basename(video_path)})")

    async with aiohttp.ClientSession() as session:
        # ── 1. TMDB ──────────────────────────────────────────
        data = await _tmdb_thumb(title, session)
        if data:
            async with aiofiles.open(out_path, "wb") as f:
                await f.write(data)
            LOGGER.info("[HDThumb] ✅ TMDB backdrop")
            return out_path

        # ── 2. Fanart.tv ─────────────────────────────────────
        data = await _fanart_thumb(title, session)
        if data:
            async with aiofiles.open(out_path, "wb") as f:
                await f.write(data)
            LOGGER.info("[HDThumb] ✅ Fanart.tv")
            return out_path

    # ── 3. ffmpeg frame extract ───────────────────────────────
    if video_path and os.path.exists(video_path):
        ok = await _ffmpeg_thumb(video_path, out_path)
        if ok:
            LOGGER.info("[HDThumb] ✅ ffmpeg frame")
            return out_path

    # ── 4. Title card (guaranteed) ────────────────────────────
    ok = await _title_card(title, out_path)
    if ok:
        LOGGER.info("[HDThumb] ✅ title card fallback")
        return out_path

    LOGGER.error("[HDThumb] All methods failed")
    return None
