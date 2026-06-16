"""
thumbnail.py — Unified HD Thumbnail System — NXTL
==================================================
Single source of truth for all thumbnail generation in the bot.
Called by uploader.py, setthumbnail.py, encode.py.

Priority chain (per file):
  1. User custom thumbnail (from settings DB)
  2. Fanart.tv: hdmovielogo + moviebackground composite  (best quality)
  3. Fanart.tv: moviethumb / tvthumb                    (logo pre-baked)
  4. TMDB: backdrop + logo composite
  5. TMDB: backdrop + title text overlay
  6. iTunes: portrait poster → landscape conversion
  7. ffmpeg frame at 30% of video duration
  8. generate_title_card()                              (always succeeds)

Key fixes vs previous version:
  - Fanart.tv movie endpoint needs IMDB ID, not TMDB ID
    → we always call /external_ids first to get imdb_id / tvdb_id
  - TV shows use tvdb_id for Fanart, not tmdb_id
  - Disk cache per (title, year) so same film isn't re-fetched
  - hd_thumb.py is now a thin shim that delegates here
  - One consistent 1280×720 JPEG ≤ 200 KB output everywhere
"""

import asyncio
import hashlib
import io
import os
import re
import time

import aiofiles
import aiohttp
import config
from bot import LOGGER

# ── Constants ─────────────────────────────────────────────────
_TMDB         = "https://api.themoviedb.org/3"
_ORIG         = "https://image.tmdb.org/t/p/original"
_W1280        = "https://image.tmdb.org/t/p/w1280"
_FANART_MOVIE = "https://webservice.fanart.tv/v3/movies"
_FANART_TV    = "https://webservice.fanart.tv/v3/tv"
_HEADERS      = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "image/webp,image/jpeg,image/png,*/*",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT   = aiohttp.ClientTimeout(total=30, connect=8)
_MIN_BYTES = 10_000
_W, _H     = 1280, 720
_MAX_KB    = 200      # Telegram reliable thumb size limit

# Disk cache directory inside downloads
_CACHE_DIR  = os.path.join(getattr(config, "DOWNLOAD_DIR", "/downloads"), ".thumb_cache")
_CACHE_SECS = 7 * 24 * 3600   # 7 days

# Font search paths (Dockerfile installs fonts-dejavu + fonts-liberation)
_FONTS_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
_FONTS_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


# ── Font helpers ──────────────────────────────────────────────

def _font(paths, size):
    from PIL import ImageFont
    for p in paths:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        try:
            bbox = draw.textbbox((0, 0), test, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(test) * 10
        if tw <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ── Disk cache ────────────────────────────────────────────────

def _cache_key(title: str, year: str | None) -> str:
    raw = f"{title.lower().strip()}_{year or ''}".encode()
    return hashlib.md5(raw).hexdigest()


def _cache_get(title: str, year: str | None) -> str | None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    key  = _cache_key(title, year)
    path = os.path.join(_CACHE_DIR, f"{key}.jpg")
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < _CACHE_SECS and os.path.getsize(path) > _MIN_BYTES:
            LOGGER.debug(f"[Thumb] cache hit: {title} ({year})")
            return path
    return None


def _cache_put(title: str, year: str | None, src: str) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    key  = _cache_key(title, year)
    dest = os.path.join(_CACHE_DIR, f"{key}.jpg")
    try:
        import shutil
        shutil.copy2(src, dest)
    except Exception:
        pass
    return dest


# ── HTTP helpers ──────────────────────────────────────────────

async def _json(session, url, params=None) -> dict:
    try:
        async with session.get(url, params=params, headers=_HEADERS,
                               timeout=_TIMEOUT) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


async def _bytes(session, url) -> bytes | None:
    try:
        async with session.get(url, headers=_HEADERS, timeout=_TIMEOUT) as r:
            if r.status == 200:
                data = await r.read()
                return data if len(data) >= 5_000 else None
    except Exception:
        pass
    return None


async def _save(session, url, dest) -> bool:
    try:
        data = await _bytes(session, url)
        if not data or len(data) < _MIN_BYTES:
            return False
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.save(dest, "JPEG", quality=95, subsampling=0, optimize=True)
        return True
    except Exception:
        try:
            if data:
                async with aiofiles.open(dest, "wb") as f:
                    await f.write(data)
                return True
        except Exception:
            pass
    return False


# ── Quality gate ──────────────────────────────────────────────

def _ok(path: str) -> bool:
    """Reject completely black, white, or solid-colour images."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(path).convert("RGB").resize((80, 45), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32)
        if arr.mean() < 5 or arr.mean() > 248:
            return False
        if arr.std() < 8:
            return False
        return True
    except Exception:
        return True


# ── Prep: resize + compress to Telegram spec ─────────────────

def prep_thumb(src: str, dest: str | None = None) -> str | None:
    """
    Convert any image → letterboxed 1280×720 JPEG ≤ 200 KB.
    Used everywhere before passing a thumbnail to Pyrogram.
    """
    if not src or not os.path.exists(src):
        return None
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size
        scale  = min(_W / w, _H / h)
        nw, nh = int(w * scale), int(h * scale)
        img    = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
        canvas.paste(img, ((_W - nw) // 2, (_H - nh) // 2))
        out = dest or (src.rsplit(".", 1)[0] + "_prepped.jpg")
        for q in (95, 88, 80, 70, 60, 50):
            canvas.save(out, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(out) <= _MAX_KB * 1024:
                break
        return out if os.path.exists(out) else None
    except Exception as e:
        LOGGER.warning(f"[Thumb] prep_thumb: {e}")
        return None


# ── TMDB search ───────────────────────────────────────────────

async def _tmdb_search(session, title, year) -> tuple[int | None, str]:
    """
    Returns (tmdb_id, media_type) where media_type is 'movie' or 'tv'.
    Searches movie first, then tv, across multiple language hints.
    """
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key:
        return None, "movie"

    for mtype in ("movie", "tv"):
        params = {
            "api_key": key,
            "query":   title,
            "include_adult": "false",
        }
        if year:
            params["year" if mtype == "movie" else "first_air_date_year"] = year

        data    = await _json(session, f"{_TMDB}/search/{mtype}", params)
        results = [r for r in data.get("results", []) if r.get("id")]
        if results:
            return results[0]["id"], mtype

    return None, "movie"


async def _external_ids(session, tmdb_id, mtype) -> dict:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    return await _json(session,
                       f"{_TMDB}/{mtype}/{tmdb_id}/external_ids",
                       {"api_key": key})


# ── Logo + backdrop compositor ────────────────────────────────

def _composite(bg_path: str, logo_data: bytes, out: str) -> bool:
    """
    Blend a transparent PNG logo onto a backdrop.
    Layout: logo bottom-left, 45% width, with gradient vignette + drop shadow.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter

        W, H = _W, _H

        # Backdrop → fill crop to W×H
        bg    = Image.open(bg_path).convert("RGB")
        bw, bh = bg.size
        sc    = max(W / bw, H / bh)
        bg    = bg.resize((int(bw * sc), int(bh * sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg    = bg.crop(((bw - W) // 2, (bh - H) // 2,
                          (bw - W) // 2 + W, (bh - H) // 2 + H))
        canvas = bg.convert("RGBA")

        # Bottom gradient for logo readability
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(H):
            t     = max(0.0, (y - H * 0.40) / (H * 0.60))
            alpha = int(190 * (t ** 1.5))
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad)

        # Logo: scale to 45% W, max 200px tall
        logo    = Image.open(io.BytesIO(logo_data)).convert("RGBA")
        lw, lh  = logo.size
        sc      = min(int(W * 0.45) / lw, 200 / lh, 1.0)
        lw, lh  = int(lw * sc), int(lh * sc)
        logo    = logo.resize((lw, lh), Image.LANCZOS)

        pad = 60
        lx, ly = pad, H - lh - pad

        # Drop shadow
        sh = Image.new("RGBA", (lw + 20, lh + 20), (0, 0, 0, 0))
        r, g, b, a = logo.split()
        sa  = a.point(lambda p: int(p * 0.55))
        sr  = Image.new("RGB", (lw, lh), (0, 0, 0))
        shi = Image.merge("RGBA", (*sr.split(), sa))
        sh.paste(shi, (10, 10))
        sh  = sh.filter(ImageFilter.GaussianBlur(radius=8))
        canvas.alpha_composite(sh, dest=(lx - 4, ly - 4))

        canvas.alpha_composite(logo, dest=(lx, ly))
        canvas.convert("RGB").save(out, "JPEG", quality=95, subsampling=0)
        return True
    except Exception as e:
        LOGGER.debug(f"[Thumb] composite failed: {e}")
        return False


# ── Text overlay on backdrop ──────────────────────────────────

def _text_overlay(bg_path: str, out: str, title: str) -> bool:
    """Apply title text at the bottom of a backdrop when no logo PNG is found."""
    try:
        from PIL import Image, ImageDraw

        img    = Image.open(bg_path).convert("RGB")
        w, h   = img.size
        sc     = max(_W / w, _H / h)
        img    = img.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
        iw, ih = img.size
        img    = img.crop(((iw - _W) // 2, (ih - _H) // 2,
                            (iw - _W) // 2 + _W, (ih - _H) // 2 + _H))
        canvas = img.convert("RGBA")

        grad = Image.new("RGBA", (_W, 200), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(200):
            alpha = int(220 * (y / 199) ** 1.5)
            gd.line([(0, y), (_W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad, dest=(0, _H - 200))

        draw  = ImageDraw.Draw(canvas)
        font  = _font(_FONTS_BOLD, 56)
        lines = _wrap(draw, title.upper(), font, _W - 120)
        lh    = 66
        ty    = _H - 38 - len(lines) * lh
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                lw   = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * 28
            lx = (_W - lw) // 2
            draw.text((lx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 200))
            draw.text((lx, ty),         line, font=font, fill=(255, 255, 255, 255))
            ty += lh

        canvas.convert("RGB").save(out, "JPEG", quality=95, subsampling=0)
        return True
    except Exception as e:
        LOGGER.debug(f"[Thumb] text_overlay failed: {e}")
        try:
            import shutil
            shutil.copy2(bg_path, out)
            return True
        except Exception:
            return False


# ── Source 1: Fanart.tv ───────────────────────────────────────

async def _fanart(session, tmdb_id, mtype, dest, title) -> bool:
    """
    Fetch from Fanart.tv.
    CRITICAL: movie endpoint uses IMDB ID, TV endpoint uses TVDB ID.
    We always call /external_ids to get the right ID first.
    """
    fa_key = getattr(config, "FANART_API_KEY", "").strip()
    if not fa_key:
        return False

    ext = await _external_ids(session, tmdb_id, mtype)

    if mtype == "movie":
        fid      = ext.get("imdb_id")            # e.g. tt1234567
        base_url = _FANART_MOVIE
        logo_key = "hdmovielogo"
        bg_key   = "moviebackground"
        th_key   = "moviethumb"
    else:
        fid      = str(ext.get("tvdb_id", ""))   # numeric string
        base_url = _FANART_TV
        logo_key = "hdtvlogo"
        bg_key   = "showbackground"
        th_key   = "tvthumb"

    if not fid:
        LOGGER.debug(f"[Thumb][Fanart] no external ID for tmdb_id={tmdb_id} type={mtype}")
        return False

    data = await _json(session, f"{base_url}/{fid}", {"api_key": fa_key})
    if not data:
        LOGGER.debug(f"[Thumb][Fanart] no data for {fid}")
        return False

    def _top(key, n=5):
        items = data.get(key, [])
        return sorted(items, key=lambda x: int(x.get("likes", 0)), reverse=True)[:n]

    logos = _top(logo_key)
    bgs   = _top(bg_key)
    thumbs = _top(th_key)

    bg_tmp = dest + ".fa_bg.tmp"

    # Best: backdrop + logo composite
    if logos and bgs:
        for bg_art in bgs:
            if not await _save(session, bg_art.get("url", ""), bg_tmp):
                continue
            if not _ok(bg_tmp):
                _rm(bg_tmp); continue
            for logo_art in logos:
                logo_data = await _bytes(session, logo_art.get("url", ""))
                if logo_data and _composite(bg_tmp, logo_data, dest):
                    _rm(bg_tmp)
                    LOGGER.info("[Thumb] ✅ Fanart: backdrop + logo composite")
                    return True
            # Logo failed — text overlay on backdrop
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: backdrop + text overlay")
                return True
            _rm(bg_tmp)

    # Good: moviethumb (logo already baked)
    for art in thumbs:
        if await _save(session, art.get("url", ""), dest) and _ok(dest):
            LOGGER.info("[Thumb] ✅ Fanart: moviethumb")
            return True

    # Fallback: plain backdrop + text
    for art in bgs:
        if await _save(session, art.get("url", ""), bg_tmp) and _ok(bg_tmp):
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: plain backdrop + text")
                return True
            _rm(bg_tmp)

    return False


# ── Source 2: TMDB ───────────────────────────────────────────

async def _tmdb(session, tmdb_id, mtype, dest, title) -> bool:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key:
        return False

    data = await _json(
        session,
        f"{_TMDB}/{mtype}/{tmdb_id}/images",
        {"api_key": key, "include_image_language": "en,hi,te,ta,null"},
    )

    backdrops = sorted(
        data.get("backdrops", []),
        key=lambda x: (float(x.get("vote_average", 0)), int(x.get("vote_count", 0))),
        reverse=True,
    )
    logos = sorted(
        [l for l in data.get("logos", []) if l.get("file_path", "").endswith(".png")],
        key=lambda x: (float(x.get("vote_average", 0)), int(x.get("vote_count", 0))),
        reverse=True,
    )

    bg_tmp = dest + ".tm_bg.tmp"

    for bd in backdrops[:5]:
        fp = bd.get("file_path", "")
        if not fp:
            continue
        if not await _save(session, _W1280 + fp, bg_tmp):
            continue
        if not _ok(bg_tmp):
            _rm(bg_tmp); continue

        for logo in logos[:5]:
            lfp       = logo.get("file_path", "")
            logo_data = await _bytes(session, _ORIG + lfp) if lfp else None
            if logo_data and _composite(bg_tmp, logo_data, dest):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ TMDB: backdrop + logo composite")
                return True

        if _text_overlay(bg_tmp, dest, title):
            _rm(bg_tmp)
            LOGGER.info("[Thumb] ✅ TMDB: backdrop + text overlay")
            return True
        _rm(bg_tmp)

    return False


# ── Source 3: iTunes portrait → landscape ────────────────────

async def _itunes(session, title, mtype, dest) -> bool:
    entity = "movie" if mtype == "movie" else "tvShow"
    for country in ("in", "us", "gb"):
        data = await _json(session, "https://itunes.apple.com/search", {
            "term":    title,
            "media":   "movie" if mtype == "movie" else "tvShow",
            "entity":  entity,
            "limit":   "6",
            "country": country,
        })
        for item in data.get("results", [])[:6]:
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if not art:
                continue
            hd  = re.sub(r"/\d+x\d+bb/", "/2000x2000bb/", art)
            tmp = dest + ".it_tmp.jpg"
            if await _save(session, hd, tmp) and _ok(tmp):
                ok = _portrait_to_landscape(tmp, dest, title)
                _rm(tmp)
                if ok:
                    LOGGER.info("[Thumb] ✅ iTunes: portrait → landscape")
                    return True
    return False


# ── Portrait → Landscape ──────────────────────────────────────

def _portrait_to_landscape(src: str, out: str, title: str = "") -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFilter
        W, H = _W, _H
        img  = Image.open(src).convert("RGB")
        iw, ih = img.size

        # Blurred bg
        sc  = max(W / iw, H / ih)
        bg  = img.resize((int(iw * sc), int(ih * sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg  = bg.crop(((bw - W) // 2, (bh - H) // 2,
                        (bw - W) // 2 + W, (bh - H) // 2 + H))
        bg  = bg.filter(ImageFilter.GaussianBlur(radius=20))
        dark = Image.new("RGB", (W, H), (0, 0, 0))
        canvas = Image.blend(bg, dark, 0.55).convert("RGBA")

        # Portrait centred
        avail_h = H - 36 - (140 if title else 40)
        avail_w = int(W * 0.52)
        sc2 = min(avail_w / iw, avail_h / ih)
        fw, fh = int(iw * sc2), int(ih * sc2)
        fg  = img.resize((fw, fh), Image.LANCZOS)

        # Shadow
        sh = Image.new("RGBA", (fw + 16, fh + 16), (0, 0, 0, 0))
        sb = Image.new("RGBA", (fw, fh), (0, 0, 0, 130))
        sh.paste(sb, (8, 8))
        sh = sh.filter(ImageFilter.GaussianBlur(radius=10))
        ox = (W - fw) // 2
        oy = 36 + (avail_h - fh) // 2
        canvas.alpha_composite(sh, dest=(max(0, ox - 8), max(0, oy - 8)))
        canvas.paste(fg, (ox, oy))

        if title:
            grad = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
            gd   = ImageDraw.Draw(grad)
            for y in range(200):
                alpha = int(225 * (y / 199) ** 1.4)
                gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
            canvas.alpha_composite(grad, dest=(0, H - 200))
            draw  = ImageDraw.Draw(canvas)
            font  = _font(_FONTS_BOLD, 56)
            lines = _wrap(draw, title.upper(), font, W - 120)
            lh    = 66
            ty    = H - 38 - len(lines) * lh
            for line in lines:
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    lw   = bbox[2] - bbox[0]
                except Exception:
                    lw = len(line) * 28
                lx = (W - lw) // 2
                draw.text((lx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 200))
                draw.text((lx, ty),         line, font=font, fill=(255, 255, 255, 255))
                ty += lh

        canvas.convert("RGB").save(out, "JPEG", quality=95, subsampling=0)
        return True
    except Exception as e:
        LOGGER.debug(f"[Thumb] portrait_to_landscape: {e}")
        return False


# ── Source 4: ffmpeg frame ────────────────────────────────────

async def _ffmpeg_frame(video: str, dest: str) -> bool:
    """Extract frame at 30% duration. Only works if file exists locally."""
    if not os.path.exists(video):
        return False
    try:
        from asyncio import create_subprocess_exec
        from asyncio.subprocess import PIPE

        proc = await create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video, stdout=PIPE, stderr=PIPE,
        )
        out, _ = await proc.communicate()
        dur = float((out.decode().strip()) or "0")
    except Exception:
        dur = 0

    seek = max(dur * 0.30, 3.0) if dur > 10 else 1.0
    cmd  = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek), "-i", video,
        "-vframes", "1",
        "-vf", f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
               f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2:black",
        "-q:v", "2", "-y", dest,
    ]
    try:
        from asyncio import create_subprocess_exec
        from asyncio.subprocess import PIPE
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        _, err = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 1024:
            LOGGER.info("[Thumb] ✅ ffmpeg frame")
            return True
    except Exception as e:
        LOGGER.debug(f"[Thumb] ffmpeg frame error: {e}")
    return False


# ── Source 5: Generated title card ───────────────────────────

def generate_title_card(title: str, dest: str,
                        year: str = "", genre: str = "") -> bool:
    """Guaranteed fallback — cinematic dark gradient card with title text."""
    try:
        import random
        from PIL import Image, ImageDraw
        W, H   = _W, _H
        canvas = Image.new("RGBA", (W, H))
        draw   = ImageDraw.Draw(canvas)

        # Deep navy gradient
        for y in range(H):
            t = y / H
            r = int(8  + (2  - 8)  * t)
            g = int(15 + (5  - 15) * t)
            b = int(35 + (12 - 35) * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

        # Film grain
        rng = random.Random(hash(title) % (2 ** 31))
        for _ in range(16000):
            x, y = rng.randint(0, W - 1), rng.randint(0, H - 1)
            br   = rng.randint(12, 38)
            draw.point((x, y), fill=(br, br, br, rng.randint(28, 65)))

        # Gold accent lines
        acc = (220, 170, 30)
        for y_pos in [H // 2 - 88, H // 2 + 88]:
            draw.line([(120, y_pos), (W - 120, y_pos)], fill=(*acc, 155), width=1)

        # Corner marks
        for cx, cy, dx, dy in [(80, 80, 1, 1), (W - 80, 80, -1, 1),
                                (80, H - 80, 1, -1), (W - 80, H - 80, -1, -1)]:
            draw.line([(cx, cy), (cx + dx * 40, cy)], fill=(*acc, 160), width=2)
            draw.line([(cx, cy), (cx, cy + dy * 40)], fill=(*acc, 160), width=2)

        # Watermark
        wm = getattr(config, "WATERMARK", "NXT HUB")
        draw.text((82, 58), f"⚡ {wm}", font=_font(_FONTS_BOLD, 20),
                  fill=(220, 170, 30, 200))

        # Title
        tfont = _font(_FONTS_BOLD, 70)
        lines = _wrap(draw, title.upper(), tfont, W - 180)
        lh    = 82
        ty    = (H - len(lines) * lh) // 2 - 20
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=tfont)
                lw   = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * 35
            lx = (W - lw) // 2
            for off, alpha in [(4, 55), (2, 95), (1, 130)]:
                draw.text((lx + off, ty + off), line, font=tfont,
                          fill=(30, 60, 120, alpha))
            draw.text((lx, ty), line, font=tfont, fill=(255, 255, 255, 255))
            ty += lh

        # Sub-line
        sub_parts = [p for p in [year, genre] if p]
        if sub_parts:
            sf  = _font(_FONTS_REG, 28)
            sub = "  ·  ".join(sub_parts)
            try:
                bbox = draw.textbbox((0, 0), sub, font=sf)
                sw   = bbox[2] - bbox[0]
            except Exception:
                sw = len(sub) * 14
            draw.text(((W - sw) // 2, ty + 10), sub, font=sf,
                      fill=(180, 180, 200, 200))

        canvas.convert("RGB").save(dest, "JPEG", quality=95, subsampling=0)
        LOGGER.info("[Thumb] ✅ title card (fallback)")
        return True
    except Exception as e:
        LOGGER.error(f"[Thumb] generate_title_card failed: {e}")
        return False


# ── Helpers ───────────────────────────────────────────────────

def _rm(path):
    try:
        os.remove(path)
    except Exception:
        pass


def _guess_title(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r"[\._\-]", " ", name)
    name = re.sub(
        r"\b(1080p|720p|480p|4K|2160p|BluRay|BDRip|WEB.?DL|WEBRip|HDTV"
        r"|x264|x265|HEVC|AAC|DD5\.1|Dual|Audio|Hindi|Tamil|Telugu|English"
        r"|Multi|S\d{2}E\d{2}|S\d{2}|E\d{2})\b.*",
        "", name, flags=re.I,
    ).strip()
    return name or "Untitled"


# ── Public API ────────────────────────────────────────────────

async def get_thumbnail(
    title: str,
    year: str | None,
    dest: str,
    title_overlay: str = "",
    video_path: str | None = None,
) -> bool:
    """
    Fetch or generate an HD thumbnail for `title`.
    Always returns True — generate_title_card() is the final guaranteed fallback.

    Args:
        title         : movie/show name to look up
        year          : optional release year string for TMDB
        dest          : output JPEG path
        title_overlay : text to draw on fallback cards (defaults to title)
        video_path    : optional local video path for ffmpeg frame fallback
    """
    label   = title_overlay or title
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    # Check disk cache
    cached = _cache_get(title, year)
    if cached:
        try:
            import shutil
            shutil.copy2(cached, dest)
            return True
        except Exception:
            pass

    LOGGER.info(f"[Thumb] Fetching thumbnail for '{title}' ({year})")

    async with aiohttp.ClientSession() as s:
        tmdb_id, mtype = await _tmdb_search(s, title, year)

        if tmdb_id:
            # 1. Fanart backdrop + logo (best)
            if await _fanart(s, tmdb_id, mtype, dest, label):
                if _ok(dest):
                    _cache_put(title, year, dest)
                    return True

            # 2. TMDB backdrop + logo / text
            if await _tmdb(s, tmdb_id, mtype, dest, label):
                if _ok(dest):
                    _cache_put(title, year, dest)
                    return True

        # 3. iTunes portrait → landscape
        mt = mtype if tmdb_id else "movie"
        if await _itunes(s, title, mt, dest):
            if _ok(dest):
                _cache_put(title, year, dest)
                return True

    # 4. ffmpeg frame from local video
    if video_path:
        if await _ffmpeg_frame(video_path, dest):
            if _ok(dest):
                return True

    # 5. Guaranteed title card
    generate_title_card(label, dest, year or "", "")
    return True


async def generate_hd_thumb(
    file_path: str,
    uid: int = 0,
    custom_thumb: str | None = None,
) -> str | None:
    """
    Compat shim used by hd_thumb.py, uploader.py, setthumbnail.py.
    Always returns a path.
    """
    tmp = os.path.join(getattr(config, "DOWNLOAD_DIR", "/downloads"), ".thumbs")
    os.makedirs(tmp, exist_ok=True)

    # Explicit override
    if custom_thumb and os.path.exists(custom_thumb):
        return prep_thumb(custom_thumb,
                          os.path.join(tmp, f"override_{int(time.time())}.jpg"))

    # User custom thumb from DB
    if uid:
        try:
            from bot.database import users_db
            s  = users_db.get_settings(uid)
            tp = s.get("thumb_path")
            if tp and os.path.exists(tp):
                return prep_thumb(tp,
                                  os.path.join(tmp, f"usr_{uid}_{int(time.time())}.jpg"))
        except Exception:
            pass

    title = _guess_title(file_path)
    try:
        from bot.utils.rename import parse_title_year
        t, year = parse_title_year(file_path)
        title = t or title
    except Exception:
        year = None

    dest = os.path.join(tmp, f"auto_{int(time.time())}.jpg")
    await get_thumbnail(title, year, dest, title_overlay=title,
                        video_path=file_path)
    return prep_thumb(dest, dest.replace(".jpg", "_hd.jpg")) or dest
