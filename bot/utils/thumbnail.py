"""
thumbnail.py — NXTL Unified Thumbnail Engine  (full rewrite)
=============================================================
Target output: 1280×720 landscape JPEG showing:
  - Movie/show backdrop as background
  - Actual movie/show logo (transparent PNG) composited on top
  - Clean gradient so logo is always readable

Priority chain:
  1. Fanart.tv  hdmovielogo  + moviebackground  (best — logo + backdrop)
  2. Fanart.tv  moviethumb                       (pre-composited landscape)
  3. TMDB       backdrop + logo (from /images)
  4. TMDB       backdrop + title text
  5. iTunes     portrait poster → landscape conversion
  6. ffmpeg     frame at 30% of video duration
  7. Title card (always succeeds)

Key design decisions:
  - Logos ALWAYS come from Fanart or TMDB /images, never synthetic text
  - Backdrop is ALWAYS landscape — portrait posters are converted
  - Disk cache keyed by md5(title+year), 30-day TTL, 500 MB cap
  - Every source goes through _ok() quality gate before accepting
"""

import asyncio
import io
import os
import re
import time

import aiofiles
import aiohttp
import config
from bot import LOGGER

# ── API endpoints ─────────────────────────────────────────────
_TMDB         = "https://api.themoviedb.org/3"
_ORIG         = "https://image.tmdb.org/t/p/original"
_W1280        = "https://image.tmdb.org/t/p/w1280"
_FANART_MOVIE = "https://webservice.fanart.tv/v3/movies"
_FANART_TV    = "https://webservice.fanart.tv/v3/tv"
_UA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT      = aiohttp.ClientTimeout(total=25, connect=8)

# ── Output spec ───────────────────────────────────────────────
_W, _H        = 1280, 720
_MAX_BYTES    = 200 * 1024     # Telegram 200 KB limit
_MIN_BYTES    = 8_000          # reject tiny/broken images

# ── Cache dir (actual policy lives in thumb_store.py) ──────────
_BASE_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")

# ── Fonts ─────────────────────────────────────────────────────
_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _font(paths, size):
    from PIL import ImageFont
    for p in paths:
        if os.path.isfile(p):
            try: return ImageFont.truetype(p, size)
            except Exception: pass
    return ImageFont.load_default()


def _rm(p):
    try: os.remove(p)
    except Exception: pass


def _ok(path: str) -> bool:
    """Reject blank, black, or solid-colour images."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(path).convert("RGB").resize((80, 45), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32)
        return 5 < arr.mean() < 248 and arr.std() > 8
    except Exception:
        return os.path.exists(path) and os.path.getsize(path) > _MIN_BYTES


def _save_jpeg(img, dest: str) -> bool:
    """Save PIL image as JPEG ≤ 200 KB."""
    try:
        for q in (95, 88, 80, 72, 62):
            img.save(dest, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(dest) <= _MAX_BYTES:
                return True
        return True   # saved, just maybe over 200 KB
    except Exception as e:
        LOGGER.debug(f"_save_jpeg: {e}")
        return False


def _landscape_crop(img, w=_W, h=_H):
    """
    Centre-crop an image to exactly w×h, scaling to fill first.
    Works on both landscape and portrait inputs.
    """
    from PIL import Image
    iw, ih = img.size
    scale  = max(w / iw, h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img    = img.resize((nw, nh), Image.LANCZOS)
    x      = (nw - w) // 2
    y      = (nh - h) // 2
    return img.crop((x, y, x + w, y + h))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DISK CACHE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Cache ─────────────────────────────────────────────────────
# Delegated to thumb_store.py — single source of truth for cache dir,
# TTL, and eviction policy. Avoids two systems writing to the same
# directory with separate (possibly diverging) logic.
from bot.utils.thumb_store import cache_get as _cache_get, cache_put as _cache_put


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HTTP HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _get_json(session, url, params=None) -> dict:
    try:
        async with session.get(url, params=params,
                               headers={"User-Agent": _UA},
                               timeout=_TIMEOUT) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception as e:
        LOGGER.debug(f"_get_json {url}: {e}")
    return {}


async def _get_bytes(session, url) -> bytes | None:
    try:
        async with session.get(url, headers={"User-Agent": _UA},
                               timeout=_TIMEOUT) as r:
            if r.status == 200:
                data = await r.read()
                return data if len(data) >= _MIN_BYTES else None
    except Exception as e:
        LOGGER.debug(f"_get_bytes {url}: {e}")
    return None


async def _download(session, url, dest) -> bool:
    data = await _get_bytes(session, url)
    if not data: return False
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return _save_jpeg(img, dest)
    except Exception:
        try:
            async with aiofiles.open(dest, "wb") as f: await f.write(data)
            return True
        except Exception:
            return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  COMPOSITOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _composite(bg_path: str, logo_bytes: bytes, dest: str, title: str = "") -> bool:
    """
    Composite a transparent PNG logo onto a backdrop.

    Layout:
      - Backdrop fills 1280×720 (letterbox-cropped)
      - Soft dark gradient covers bottom 55% for contrast
      - Logo placed bottom-left, max 500px wide × 180px tall
      - Logo is white-normalized so dark logos stay visible on dark bgs
      - Drop shadow underneath logo
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

        W, H = _W, _H

        # ── Background ───────────────────────────────────────
        bg     = Image.open(bg_path).convert("RGB")
        canvas = _landscape_crop(bg, W, H).convert("RGBA")

        # ── Cinematic gradient (bottom 60%) ───────────────────
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(H):
            t     = max(0.0, (y - H * 0.38) / (H * 0.62))
            alpha = int(210 * (t ** 1.4))
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad)

        # ── Logo ─────────────────────────────────────────────
        logo   = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        lw, lh = logo.size

        # Scale: max 500px wide, max 180px tall
        max_lw, max_lh = 500, 180
        sc   = min(max_lw / lw, max_lh / lh, 1.0)
        lw   = max(int(lw * sc), 1)
        lh   = max(int(lh * sc), 1)
        logo = logo.resize((lw, lh), Image.LANCZOS)

        # Normalize: ensure logo is bright enough on dark gradient
        # Extract alpha, boost brightness of RGB channels
        r, g, b, a = logo.split()
        rgb = Image.merge("RGB", (r, g, b))
        rgb = ImageEnhance.Brightness(rgb).enhance(1.15)
        r, g, b = rgb.split()
        logo = Image.merge("RGBA", (r, g, b, a))

        # Position: bottom-left with padding
        pad = 52
        lx  = pad
        ly  = H - lh - pad

        # Drop shadow
        shadow = Image.new("RGBA", (lw + 24, lh + 24), (0, 0, 0, 0))
        mask   = a.point(lambda p: int(p * 0.5))
        black  = Image.new("RGB", (lw, lh), (0, 0, 0))
        sh_img = Image.merge("RGBA", (*black.split(), mask))
        shadow.paste(sh_img, (12, 12))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10))
        canvas.alpha_composite(shadow, dest=(max(0, lx - 8), max(0, ly - 8)))

        canvas.alpha_composite(logo, dest=(lx, ly))

        final = canvas.convert("RGB")
        return _save_jpeg(final, dest)

    except Exception as e:
        LOGGER.debug(f"_composite: {e}")
        return False


def _text_overlay(bg_path: str, dest: str, title: str) -> bool:
    """Backdrop + styled title text — used only when no logo PNG found."""
    try:
        from PIL import Image, ImageDraw

        W, H   = _W, _H
        bg     = Image.open(bg_path).convert("RGB")
        canvas = _landscape_crop(bg, W, H).convert("RGBA")

        grad = Image.new("RGBA", (W, 240), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(240):
            alpha = int(230 * (y / 239) ** 1.3)
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad, dest=(0, H - 240))

        draw  = ImageDraw.Draw(canvas)
        font  = _font(_BOLD, 64)
        words = title.upper().split()
        lines, cur = [], ""
        for w in words:
            test = f"{cur} {w}".strip()
            try:
                bw = draw.textbbox((0, 0), test, font=font)[2]
            except Exception:
                bw = len(test) * 30
            if bw <= W - 120: cur = test
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)

        lh = 74
        ty = H - 42 - len(lines) * lh
        for line in lines:
            try: lw = draw.textbbox((0, 0), line, font=font)[2]
            except Exception: lw = len(line) * 32
            lx = (W - lw) // 2
            draw.text((lx + 3, ty + 3), line, font=font, fill=(0, 0, 0, 180))
            draw.text((lx, ty),         line, font=font, fill=(255, 255, 255, 255))
            ty += lh

        return _save_jpeg(canvas.convert("RGB"), dest)
    except Exception as e:
        LOGGER.debug(f"_text_overlay: {e}")
        try:
            import shutil; shutil.copy2(bg_path, dest); return True
        except Exception: return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TMDB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _tmdb_search(session, title: str, year) -> tuple[int | None, str]:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key: return None, "movie"
    for mtype in ("movie", "tv"):
        params = {"api_key": key, "query": title, "include_adult": "false"}
        if year:
            params["year" if mtype == "movie" else "first_air_date_year"] = year
        data = await _get_json(session, f"{_TMDB}/search/{mtype}", params)
        results = [r for r in data.get("results", []) if r.get("id")]
        if results:
            return results[0]["id"], mtype
    return None, "movie"


async def _external_ids(session, tmdb_id, mtype) -> dict:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    return await _get_json(session, f"{_TMDB}/{mtype}/{tmdb_id}/external_ids",
                           {"api_key": key})


async def _tmdb_thumb(session, tmdb_id, mtype, dest, title) -> bool:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key: return False

    data = await _get_json(session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
                           {"api_key": key,
                            "include_image_language": "en,hi,te,ta,null"})

    def _sort(lst):
        return sorted(lst,
                      key=lambda x: (float(x.get("vote_average", 0)),
                                     int(x.get("vote_count", 0))),
                      reverse=True)

    backdrops = _sort(data.get("backdrops", []))
    logos     = _sort([l for l in data.get("logos", [])
                       if l.get("file_path", "").endswith(".png")])

    bg_tmp = dest + ".tm_bg.tmp"

    for bd in backdrops[:6]:
        fp = bd.get("file_path", "")
        if not fp: continue
        if not await _download(session, _W1280 + fp, bg_tmp): continue
        if not _ok(bg_tmp): _rm(bg_tmp); continue

        # Try each logo
        for logo in logos[:6]:
            lfp  = logo.get("file_path", "")
            lbytes = await _get_bytes(session, _ORIG + lfp) if lfp else None
            if lbytes and _composite(bg_tmp, lbytes, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ TMDB: backdrop + logo")
                return True

        # No logo → text overlay
        if _text_overlay(bg_tmp, dest, title):
            _rm(bg_tmp)
            LOGGER.info("[Thumb] ✅ TMDB: backdrop + text")
            return True
        _rm(bg_tmp)

    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FANART.TV
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _fanart_thumb(session, tmdb_id, mtype, dest, title) -> bool:
    fa_key = getattr(config, "FANART_API_KEY", "").strip()
    if not fa_key: return False

    # Get external IDs (Fanart needs IMDB id for movies, TVDB id for TV)
    ext = await _external_ids(session, tmdb_id, mtype)

    if mtype == "movie":
        fid      = ext.get("imdb_id", "")
        base_url = _FANART_MOVIE
        # Keys in priority order — all possible landscape/logo types
        logo_keys = ["hdmovielogo", "movielogo"]
        bg_keys   = ["moviebackground", "moviethumb"]
        # moviethumb is already composited (logo + backdrop) — ideal
        precomp_keys = ["moviethumb"]
    else:
        fid      = str(ext.get("tvdb_id", ""))
        base_url = _FANART_TV
        logo_keys    = ["hdtvlogo", "tvlogo", "clearlogo"]
        bg_keys      = ["showbackground", "tvthumb"]
        precomp_keys = ["tvthumb"]

    if not fid:
        LOGGER.debug(f"[Thumb][Fanart] no external ID tmdb={tmdb_id}")
        return False

    data = await _get_json(session, f"{base_url}/{fid}", {"api_key": fa_key})
    if not data:
        LOGGER.debug(f"[Thumb][Fanart] no data for {fid}")
        return False

    def _top(key, n=6):
        items = data.get(key, [])
        return sorted(items, key=lambda x: int(x.get("likes", 0)), reverse=True)[:n]

    # Collect all available logos and backgrounds
    logos   = []
    for k in logo_keys:
        logos.extend(_top(k, 4))

    bgs     = []
    for k in bg_keys:
        bgs.extend(_top(k, 4))

    precomp = []
    for k in precomp_keys:
        precomp.extend(_top(k, 3))

    bg_tmp = dest + ".fa_bg.tmp"

    # ── Strategy 1: backdrop + logo composite ────────────────
    if logos and bgs:
        for bg_art in bgs[:4]:
            url = bg_art.get("url", "")
            if not url: continue
            if not await _download(session, url, bg_tmp): continue
            if not _ok(bg_tmp): _rm(bg_tmp); continue

            for logo_art in logos[:4]:
                lurl   = logo_art.get("url", "")
                lbytes = await _get_bytes(session, lurl) if lurl else None
                if lbytes and _composite(bg_tmp, lbytes, dest, title):
                    _rm(bg_tmp)
                    LOGGER.info("[Thumb] ✅ Fanart: backdrop + logo")
                    return True

            # Background found but no logo worked → text overlay
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: backdrop + text")
                return True
            _rm(bg_tmp)

    # ── Strategy 2: pre-composited moviethumb ────────────────
    for art in precomp[:3]:
        url = art.get("url", "")
        if not url: continue
        if await _download(session, url, dest) and _ok(dest):
            # Ensure it's exactly 1280×720
            try:
                from PIL import Image
                img = Image.open(dest)
                if img.size != (_W, _H):
                    img = _landscape_crop(img.convert("RGB"))
                    _save_jpeg(img, dest)
            except Exception: pass
            LOGGER.info("[Thumb] ✅ Fanart: moviethumb (pre-composited)")
            return True

    # ── Strategy 3: background only + text ───────────────────
    for bg_art in bgs[:3]:
        url = bg_art.get("url", "")
        if not url: continue
        if await _download(session, url, bg_tmp) and _ok(bg_tmp):
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: bg + text fallback")
                return True
            _rm(bg_tmp)

    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ITUNES  (portrait poster → landscape)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _itunes_thumb(session, title: str, mtype: str, dest: str) -> bool:
    entity = "movie" if mtype == "movie" else "tvShow"
    for country in ("in", "us", "gb"):
        data = await _get_json(session, "https://itunes.apple.com/search", {
            "term": title, "media": "movie" if mtype == "movie" else "tvShow",
            "entity": entity, "limit": "8", "country": country,
        })
        for item in data.get("results", [])[:8]:
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if not art: continue
            hd  = re.sub(r"/\d+x\d+bb/", "/3000x3000bb/", art)
            tmp = dest + ".it_tmp.jpg"
            if await _download(session, hd, tmp) and _ok(tmp):
                ok = _portrait_to_landscape(tmp, dest, title)
                _rm(tmp)
                if ok:
                    LOGGER.info("[Thumb] ✅ iTunes → landscape")
                    return True
    return False


def _portrait_to_landscape(src: str, out: str, title: str = "") -> bool:
    """
    Convert a portrait poster to a cinematic landscape thumbnail.
    Poster is placed right-of-centre on a blurred+darkened version of itself.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter

        W, H = _W, _H
        img  = Image.open(src).convert("RGB")
        iw, ih = img.size

        # Blurred dark background
        sc  = max(W / iw, H / ih)
        bg  = img.resize((int(iw * sc), int(ih * sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg  = bg.crop(((bw - W) // 2, (bh - H) // 2,
                        (bw - W) // 2 + W, (bh - H) // 2 + H))
        bg  = bg.filter(ImageFilter.GaussianBlur(radius=22))
        dark = Image.new("RGB", (W, H), (0, 0, 0))
        canvas = Image.blend(bg, dark, 0.60).convert("RGBA")

        # Poster: right-of-centre, with 20px margin
        avail_h  = H - 32
        max_pw   = int(W * 0.48)
        sc2      = min(max_pw / iw, avail_h / ih)
        fw, fh   = int(iw * sc2), int(ih * sc2)
        poster   = img.resize((fw, fh), Image.LANCZOS)
        px       = W - fw - 20
        py       = (H - fh) // 2

        # Soft shadow behind poster
        sh = Image.new("RGBA", (fw + 20, fh + 20), (0, 0, 0, 0))
        sb = Image.new("RGBA", (fw, fh), (0, 0, 0, 140))
        sh.paste(sb, (10, 10))
        sh = sh.filter(ImageFilter.GaussianBlur(radius=12))
        canvas.alpha_composite(sh, dest=(max(0, px - 8), max(0, py - 8)))
        canvas.paste(poster, (px, py))

        # Title text bottom-left
        if title:
            grad = Image.new("RGBA", (W, 220), (0, 0, 0, 0))
            gd   = ImageDraw.Draw(grad)
            for y in range(220):
                alpha = int(230 * (y / 219) ** 1.3)
                gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
            canvas.alpha_composite(grad, dest=(0, H - 220))

            draw  = ImageDraw.Draw(canvas)
            font  = _font(_BOLD, 58)
            words = title.upper().split()
            lines, cur = [], ""
            max_tw = px - 30
            for w in words:
                test = f"{cur} {w}".strip()
                try: tw = draw.textbbox((0, 0), test, font=font)[2]
                except Exception: tw = len(test) * 28
                if tw <= max_tw: cur = test
                else:
                    if cur: lines.append(cur)
                    cur = w
            if cur: lines.append(cur)
            lh = 66
            ty = H - 36 - len(lines) * lh
            for line in lines:
                try: lw2 = draw.textbbox((0, 0), line, font=font)[2]
                except Exception: lw2 = len(line) * 29
                lx = 46
                draw.text((lx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 200))
                draw.text((lx, ty),         line, font=font, fill=(255, 255, 255, 255))
                ty += lh

        return _save_jpeg(canvas.convert("RGB"), out)
    except Exception as e:
        LOGGER.debug(f"_portrait_to_landscape: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FFMPEG FRAME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _ffmpeg_frame(video: str, dest: str) -> bool:
    if not video or not os.path.exists(video): return False
    try:
        from asyncio import create_subprocess_exec
        from asyncio.subprocess import PIPE
        pr = await create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video,
            stdout=PIPE, stderr=PIPE,
        )
        out, _ = await pr.communicate()
        dur = float(out.decode().strip() or "0")
    except Exception: dur = 0

    seek = max(dur * 0.30, 3.0) if dur > 10 else 1.0
    cmd  = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek), "-i", video, "-vframes", "1",
        "-vf", f"scale={_W}:{_H}:force_original_aspect_ratio=decrease,"
               f"pad={_W}:{_H}:(ow-iw)/2:(oh-ih)/2:black",
        "-q:v", "2", "-y", dest,
    ]
    try:
        from asyncio import create_subprocess_exec
        from asyncio.subprocess import PIPE
        pr = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        await pr.communicate()
        if os.path.exists(dest) and os.path.getsize(dest) > 2048:
            LOGGER.info("[Thumb] ✅ ffmpeg frame")
            return True
    except Exception as e:
        LOGGER.debug(f"_ffmpeg_frame: {e}")
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TITLE CARD  (guaranteed fallback)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_title_card(title: str, dest: str, year: str = "", genre: str = "") -> bool:
    try:
        import random
        from PIL import Image, ImageDraw

        W, H = _W, _H
        rng  = random.Random(hash(title) % (2**31))
        canvas = Image.new("RGBA", (W, H))
        draw   = ImageDraw.Draw(canvas)

        # Deep navy gradient
        for y in range(H):
            t = y / H
            draw.line([(0, y), (W, y)],
                      fill=(int(8 + (2 - 8) * t), int(15 + (5 - 15) * t),
                            int(35 + (12 - 35) * t), 255))

        # Film grain
        for _ in range(14000):
            x, y = rng.randint(0, W-1), rng.randint(0, H-1)
            br   = rng.randint(12, 36)
            draw.point((x, y), fill=(br, br, br, rng.randint(25, 60)))

        # Gold accent lines
        acc = (210, 160, 30)
        for yp in [H // 2 - 92, H // 2 + 92]:
            draw.line([(100, yp), (W - 100, yp)], fill=(*acc, 145), width=1)

        # Corner marks
        for cx, cy, dx, dy in [(72,72,1,1),(W-72,72,-1,1),(72,H-72,1,-1),(W-72,H-72,-1,-1)]:
            draw.line([(cx,cy),(cx+dx*38,cy)], fill=(*acc,150), width=2)
            draw.line([(cx,cy),(cx,cy+dy*38)], fill=(*acc,150), width=2)

        # Watermark
        wm = getattr(config, "WATERMARK", "NXT HUB")
        draw.text((80, 52), f"⚡ {wm}", font=_font(_BOLD, 20), fill=(210, 160, 30, 200))

        # Title
        tfont = _font(_BOLD, 72)
        words = title.upper().split()
        lines, cur = [], ""
        for w in words:
            test = f"{cur} {w}".strip()
            try: tw = draw.textbbox((0,0), test, font=tfont)[2]
            except Exception: tw = len(test) * 34
            if tw <= W - 160: cur = test
            else:
                if cur: lines.append(cur)
                cur = w
        if cur: lines.append(cur)

        lh = 84
        ty = (H - len(lines) * lh) // 2 - 20
        for line in lines:
            try: lw = draw.textbbox((0,0), line, font=tfont)[2]
            except Exception: lw = len(line) * 35
            lx = (W - lw) // 2
            for off, al in [(4,50),(2,90),(1,125)]:
                draw.text((lx+off, ty+off), line, font=tfont, fill=(30,60,120,al))
            draw.text((lx, ty), line, font=tfont, fill=(255, 255, 255, 255))
            ty += lh

        sub = "  ·  ".join(p for p in [year, genre] if p)
        if sub:
            sf  = _font(_REG, 28)
            try: sw = draw.textbbox((0,0), sub, font=sf)[2]
            except Exception: sw = len(sub) * 14
            draw.text(((W-sw)//2, ty+8), sub, font=sf, fill=(175, 175, 195, 200))

        return _save_jpeg(canvas.convert("RGB"), dest)
    except Exception as e:
        LOGGER.error(f"generate_title_card: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PUBLIC API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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


async def get_thumbnail(
    title: str,
    year,
    dest: str,
    title_overlay: str = "",
    video_path: str | None = None,
) -> bool:
    """
    Fetch or generate a 1280×720 landscape thumbnail with actual movie logo.
    Always returns True — generate_title_card() is the final guaranteed fallback.
    """
    label = title_overlay or title
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    # Check cache
    cached = _cache_get(title, year)
    if cached:
        try:
            import shutil; shutil.copy2(cached, dest); return True
        except Exception: pass

    LOGGER.info(f"[Thumb] Fetching: '{title}' ({year})")

    async with aiohttp.ClientSession() as s:
        tmdb_id, mtype = await _tmdb_search(s, title, year)

        if tmdb_id:
            LOGGER.info(f"[Thumb] TMDB match: id={tmdb_id} type={mtype}")

            # 1. Fanart: backdrop + logo (best quality)
            if await _fanart_thumb(s, tmdb_id, mtype, dest, label):
                if _ok(dest):
                    _cache_put(title, year, dest); return True

            # 2. TMDB: backdrop + logo
            if await _tmdb_thumb(s, tmdb_id, mtype, dest, label):
                if _ok(dest):
                    _cache_put(title, year, dest); return True

        # 3. iTunes portrait → landscape
        mt = mtype if tmdb_id else "movie"
        if await _itunes_thumb(s, title, mt, dest):
            if _ok(dest):
                _cache_put(title, year, dest); return True

    # 4. ffmpeg frame
    if video_path and await _ffmpeg_frame(video_path, dest):
        if _ok(dest): return True

    # 5. Title card (always succeeds)
    generate_title_card(label, dest, year or "")
    return True


async def generate_hd_thumb(
    file_path: str,
    uid: int = 0,
    custom_thumb: str | None = None,
) -> str | None:
    """
    Main entry point for the uploader.
    Returns path to a 1280×720 JPEG thumbnail, always.

    Priority:
      1. Explicit custom_thumb passed by caller
      2. User's saved custom thumbnail (from /settings)
      3. Fanart.tv backdrop + logo  ← best quality
      4. TMDB backdrop + logo
      5. iTunes portrait → landscape
      6. ffmpeg frame
      7. Title card (always succeeds)
    """
    from bot.utils.thumb_store import TMP_DIR as tmp
    os.makedirs(tmp, exist_ok=True)

    # 1. Explicit override
    if custom_thumb and os.path.exists(custom_thumb):
        dest = os.path.join(tmp, f"custom_{int(time.time())}.jpg")
        try:
            from PIL import Image
            img = _landscape_crop(Image.open(custom_thumb).convert("RGB"))
            _save_jpeg(img, dest)
            return dest
        except Exception:
            pass

    # 2. User's saved custom thumbnail from /settings
    if uid:
        try:
            from bot.database import users_db
            s  = users_db.get_settings(uid)
            tp = s.get("thumb_path")
            if tp and os.path.exists(tp):
                dest = os.path.join(tmp, f"usr_{uid}_{int(time.time())}.jpg")
                from PIL import Image
                img = _landscape_crop(Image.open(tp).convert("RGB"))
                _save_jpeg(img, dest)
                return dest
        except Exception:
            pass

    # 3–7. Auto-fetch: use the SAME parse_title_year as the rest of the bot
    #       (it now strips language tags, site prefixes, dangling punctuation)
    title = _guess_title(file_path)
    year  = None
    try:
        from bot.utils.rename import parse_title_year
        t, year = parse_title_year(file_path)
        if t and t != "Untitled":
            title = t
    except Exception:
        pass

    dest = os.path.join(tmp, f"auto_{int(time.time())}.jpg")
    await get_thumbnail(title, year, dest, title_overlay=title, video_path=file_path)
    return dest if os.path.exists(dest) else None


# prep_thumb is imported from thumb_store — kept here for any direct callers
def prep_thumb(src: str, dest: str | None = None) -> str | None:
    from bot.utils.thumb_store import prep_for_upload
    return prep_for_upload(src, dest)
