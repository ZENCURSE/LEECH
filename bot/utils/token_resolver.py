"""
token_resolver.py — Dynamic prefix/suffix token expansion.

Supported tokens:
  {name}      → clean filename stem (no extension, no junk chars)
  {ext}       → file extension without dot      (e.g. mp4, mkv)
  {size}      → human-readable file size        (e.g. 1.23 GB)
  {language}  → audio language codes joined     (e.g. eng+hin+tam)
  {time}      → media duration                  (e.g. 1h 23m 45s)
  {quality}   → resolution / quality tag        (e.g. 1080p, 4K, Audio)
  {codec}     → video codec                     (e.g. H264, HEVC, AV1)
  {audio}     → audio codec                     (e.g. AAC, AC3, DTS)
  {fps}       → frame rate (rounded)            (e.g. 24, 30, 60)
  {date}      → today's date                    (e.g. 2026-06-02)

Unknown tokens are left unchanged so users see exactly what they typed.
ffprobe is only run if at least one media token is actually present.
"""

import os
import re
import json
import asyncio
from datetime import date

from bot.utils.size_utils import human_size


# ──────────────────────────────────────────────────────────────
#  ffprobe
# ──────────────────────────────────────────────────────────────

async def _ffprobe(path: str) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_streams", "-show_format",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return json.loads(stdout.decode(errors="replace"))
    except Exception:
        return {}


def _streams(data: dict, codec_type: str) -> list:
    return [s for s in data.get("streams", []) if s.get("codec_type") == codec_type]


# ──────────────────────────────────────────────────────────────
#  Sync token resolvers (no ffprobe needed)
# ──────────────────────────────────────────────────────────────

def _tok_name(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[.\-_(){}\[\]]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _tok_ext(path: str) -> str:
    return os.path.splitext(path)[1].lstrip(".").lower() or "file"


def _tok_size(path: str) -> str:
    try:
        return human_size(os.path.getsize(path))
    except Exception:
        return "?"


def _tok_date() -> str:
    return date.today().isoformat()


# ──────────────────────────────────────────────────────────────
#  Async token resolvers (need ffprobe data)
# ──────────────────────────────────────────────────────────────

async def _tok_language(path: str, data: dict) -> str:
    langs = []
    seen: set[str] = set()
    for s in _streams(data, "audio"):
        lang = (s.get("tags") or {}).get("language", "").lower().strip()
        if lang and lang not in ("und", "unknown", "") and lang not in seen:
            langs.append(lang)
            seen.add(lang)
    return "+".join(langs) if langs else "unknown"


async def _tok_time(path: str, data: dict) -> str:
    try:
        dur = float(data.get("format", {}).get("duration", 0))
        if dur <= 0:
            return "?"
        h, rem = divmod(int(dur), 3600)
        m, s   = divmod(rem, 60)
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        elif m:
            return f"{m}m {s:02d}s"
        return f"{s}s"
    except Exception:
        return "?"


async def _tok_quality(path: str, data: dict) -> str:
    for s in _streams(data, "video"):
        h = s.get("height", 0)
        if h >= 2160: return "4K"
        if h >= 1080: return "1080p"
        if h >= 720:  return "720p"
        if h >= 480:  return "480p"
        if h >= 360:  return "360p"
        if h > 0:     return f"{h}p"
    if _streams(data, "audio"):
        return "Audio"
    # Fallback: scan filename
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    for tag in ("2160p", "4k", "1080p", "720p", "480p", "360p"):
        if tag in stem:
            return tag
    return "?"


_VIDEO_CODECS = {
    "h264": "H264", "avc": "H264",
    "hevc": "HEVC", "h265": "HEVC",
    "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
    "mpeg4": "MPEG4", "mpeg2video": "MPEG2",
}

async def _tok_codec(path: str, data: dict) -> str:
    for s in _streams(data, "video"):
        c = (s.get("codec_name") or "").lower()
        if c and c not in {"mjpeg", "png", "bmp", "gif"}:
            return _VIDEO_CODECS.get(c, c.upper())
    return "?"


_AUDIO_CODECS = {
    "aac": "AAC", "mp3": "MP3", "ac3": "AC3", "eac3": "E-AC3",
    "dts": "DTS", "flac": "FLAC", "vorbis": "Vorbis", "opus": "Opus",
    "truehd": "TrueHD", "alac": "ALAC",
    "pcm_s16le": "PCM", "pcm_s24le": "PCM",
}

async def _tok_audio(path: str, data: dict) -> str:
    for s in _streams(data, "audio"):
        c = (s.get("codec_name") or "").lower()
        if c:
            return _AUDIO_CODECS.get(c, c.upper())
    return "?"


async def _tok_fps(path: str, data: dict) -> str:
    for s in _streams(data, "video"):
        r = s.get("r_frame_rate", "0/1")
        try:
            num, den = r.split("/")
            fps = round(int(num) / max(int(den), 1))
            if fps > 0:
                return str(fps)
        except Exception:
            pass
    return "?"


# ──────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────

_ASYNC_TOKENS = frozenset({"language", "time", "quality", "codec", "audio", "fps"})
_SYNC_TOKENS  = frozenset({"name", "ext", "size", "date"})


async def resolve_tokens(template: str, file_path: str) -> str:
    """
    Replace all {token} occurrences in *template* using info from *file_path*.
    ffprobe is only called when an async token is present.
    Unknown tokens are left as-is.
    """
    if not template:
        return template

    used = set(re.findall(r"\{(\w+)\}", template))
    if not used:
        return template

    data = await _ffprobe(file_path) if used & _ASYNC_TOKENS else {}

    r = template
    if "name"     in used: r = r.replace("{name}",     _tok_name(file_path))
    if "ext"      in used: r = r.replace("{ext}",      _tok_ext(file_path))
    if "size"     in used: r = r.replace("{size}",     _tok_size(file_path))
    if "date"     in used: r = r.replace("{date}",     _tok_date())
    if "language" in used: r = r.replace("{language}", await _tok_language(file_path, data))
    if "time"     in used: r = r.replace("{time}",     await _tok_time(file_path, data))
    if "quality"  in used: r = r.replace("{quality}",  await _tok_quality(file_path, data))
    if "codec"    in used: r = r.replace("{codec}",    await _tok_codec(file_path, data))
    if "audio"    in used: r = r.replace("{audio}",    await _tok_audio(file_path, data))
    if "fps"      in used: r = r.replace("{fps}",      await _tok_fps(file_path, data))
    return r


async def resolve_prefix_suffix(prefix: str, suffix: str,
                                file_path: str) -> tuple[str, str]:
    """Resolve tokens in both prefix and suffix. Returns (prefix, suffix)."""
    rp = await resolve_tokens(prefix or "", file_path)
    rs = await resolve_tokens(suffix or "", file_path)
    return rp, rs
