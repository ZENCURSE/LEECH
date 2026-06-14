"""
Media utilities — ported from WZML-X wzv3.
Uses ffprobe to detect video/audio/image types and extract thumbnails.
Falls back gracefully if ffprobe/ffmpeg not available.
"""
import asyncio
import json
import os
import time
import mimetypes

THUMB_DIR = "/downloads/thumbnails"


async def _cmd(args: list[str]) -> tuple[str, str, int]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
    except asyncio.TimeoutError:
        return "", "timeout", 1
    except FileNotFoundError:
        return "", "not found", 1
    except Exception as e:
        return "", str(e), 1


async def get_document_type(path: str) -> tuple[bool, bool, bool]:
    """Returns (is_video, is_audio, is_image)."""
    is_video = is_audio = is_image = False

    # Quick mime check first
    mime, _ = mimetypes.guess_type(path)
    if mime:
        if mime.startswith("image"):
            return False, False, True
        if mime.startswith("audio"):
            is_audio = True

    # Use ffprobe for definitive detection
    out, _, rc = await _cmd([
        "ffprobe", "-hide_banner", "-loglevel", "error",
        "-print_format", "json", "-show_streams", path,
    ])
    if rc != 0 or not out:
        # Fallback to mime
        if mime:
            return mime.startswith("video"), mime.startswith("audio"), False
        return False, False, False

    try:
        data = json.loads(out)
    except Exception:
        return False, False, False

    for stream in data.get("streams", []):
        ctype = stream.get("codec_type", "")
        if ctype == "video":
            codec = stream.get("codec_name", "").lower()
            if codec not in {"mjpeg", "png", "bmp", "gif"}:
                is_video = True
        elif ctype == "audio":
            is_audio = True

    return is_video, is_audio, is_image


async def get_media_info(path: str) -> tuple[int, str | None, str | None]:
    """Returns (duration_seconds, artist, title)."""
    out, _, rc = await _cmd([
        "ffprobe", "-hide_banner", "-loglevel", "error",
        "-print_format", "json", "-show_format", path,
    ])
    if rc != 0 or not out:
        return 0, None, None
    try:
        data  = json.loads(out)
        fmt   = data.get("format", {})
        dur   = round(float(fmt.get("duration", 0)))
        tags  = fmt.get("tags", {})
        artist = tags.get("artist") or tags.get("ARTIST") or tags.get("Artist")
        title  = tags.get("title") or tags.get("TITLE") or tags.get("Title")
        return dur, artist, title
    except Exception:
        return 0, None, None


async def get_video_thumbnail(video_file: str, duration: int | None) -> str | None:
    """
    Extract a high-quality thumbnail frame from a video.

    Two-pass strategy:
      Pass 1 — extract full-resolution frame at mid-point (no downscale)
      Pass 2 — PIL: resize to 1280×720 max keeping aspect, save JPEG q=95
               Fallback: ffmpeg scale with -q:v 1 (highest ffmpeg quality)

    Keeping the image at 1280px wide prevents Telegram from upscaling it
    (upscaling = more artefacts). q=95 / subsampling=0 keeps chroma detail.
    """
    os.makedirs(THUMB_DIR, exist_ok=True)
    raw    = os.path.join(THUMB_DIR, f"{time.time()}_raw.jpg")
    output = os.path.join(THUMB_DIR, f"{time.time()}.jpg")

    if duration is None:
        duration = (await get_media_info(video_file))[0]
    seek = max((duration or 6) // 2, 1)

    # Pass 1: extract full-res frame, no scaling
    _, _, rc = await _cmd([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek), "-i", video_file,
        "-vf", "thumbnail",
        "-q:v", "1",       # highest quality (1 = best, 31 = worst)
        "-frames:v", "1", raw,
    ])

    if rc != 0 or not os.path.exists(raw):
        return None

    # Pass 2: PIL high-quality resize to 1280×720 max
    output = _hq_resize_thumb(raw, output, max_w=1280, max_h=720)
    try: os.remove(raw)
    except Exception: pass
    return output


async def get_audio_thumbnail(audio_file: str) -> str | None:
    """Extract embedded cover art from audio, then apply HQ resize."""
    os.makedirs(THUMB_DIR, exist_ok=True)
    raw    = os.path.join(THUMB_DIR, f"{time.time()}_raw.jpg")
    output = os.path.join(THUMB_DIR, f"{time.time()}.jpg")
    _, _, rc = await _cmd([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", audio_file, "-an", "-vcodec", "copy", raw,
    ])
    if rc != 0 or not os.path.exists(raw):
        return None
    output = _hq_resize_thumb(raw, output, max_w=1280, max_h=1280)
    try: os.remove(raw)
    except Exception: pass
    return output


def _hq_resize_thumb(src: str, dest: str, max_w: int = 1280, max_h: int = 720) -> str:
    """
    Resize image to fit within max_w×max_h, save as JPEG quality=95 subsampling=0.
    subsampling=0 keeps 4:4:4 chroma — prevents the muddy colour smearing
    Telegram's server introduces when it re-encodes low-quality thumbs.
    Falls back to returning src unchanged if PIL unavailable.
    """
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        w, h = img.size
        scale = min(max_w / w, max_h / h, 1.0)   # never upscale
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        img.save(dest, "JPEG", quality=95, subsampling=0, optimize=True)
        return dest
    except Exception:
        return src   # return original if PIL fails


# ── get_streams — ffprobe all stream info (added from NEO-WZML) ──
import json as _json
from asyncio import create_subprocess_exec as _cse
from asyncio.subprocess import PIPE as _PIPE

async def get_streams(file_path: str):
    """Return list of stream dicts from ffprobe, or None on error."""
    cmd = [
        "ffprobe", "-hide_banner", "-loglevel", "error",
        "-print_format", "json", "-show_streams", file_path,
    ]
    proc = await _cse(*cmd, stdout=_PIPE, stderr=_PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        return _json.loads(stdout)["streams"]
    except (KeyError, Exception):
        return None
