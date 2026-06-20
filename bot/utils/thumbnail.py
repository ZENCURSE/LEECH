"""
thumbnail.py — NXTL Unified Thumbnail Engine (rewrite v3)
==========================================================
Target output: 1280×720 landscape JPEG showing the ACTUAL movie poster
with real logo/text from the original movie artwork.

Priority chain:
  1. Fanart.tv  hdmovielogo + moviebackground  (best — real logo PNG + clean backdrop)
               Falls back to Metahub logo if Fanart has no logo
  2. TMDB       textless backdrop + logo PNG   (iso_639_1=null backdrops only)
               Falls back to Metahub logo if TMDB has no logo
  3. TMDB       actual movie poster (portrait)  → cinema landscape conversion
  4. OMDB       poster URL                      → cinema landscape conversion
  5. Fanart.tv  moviethumb (pre-composited, used AS-IS — no extra logo)
  6. iTunes     portrait poster                 → landscape conversion
  7. ffmpeg     frame at 30% of video duration
  (no title card fallback — returns False/None if nothing found)

Key design decisions:
  - DOUBLE LOGO PREVENTION: logo PNG is ONLY composited onto textless/clean
    backdrops (iso_639_1=null for TMDB, moviebackground for Fanart).
    English/language backdrops that already have the title baked in are
    NEVER used as a base for logo compositing.
  - Fanart moviethumb (pre-composited) is used as-is — no further logo added.
  - Metahub (metahub.space) is tried as a logo source ONLY when Fanart and
    TMDB both fail to provide a logo, ensuring a single logo composite.
  - Cover (cover= param) is saved at FULL QUALITY (up to 5 MB) — not capped at 200 KB
  - thumb= (file list preview) is still capped at 200 KB / 320×320
  - Cache keyed by md5(title+year), 30-day TTL, 500 MB cap
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
_W780         = "https://image.tmdb.org/t/p/w780"
_FANART_MOVIE = "https://webservice.fanart.tv/v3/movies"
_FANART_TV    = "https://webservice.fanart.tv/v3/tv"
_METAHUB      = "https://metahub.space/logo/medium/{imdb_id}/img"
_OMDB         = "https://www.omdbapi.com/"
_UA           = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT      = aiohttp.ClientTimeout(total=30, connect=10)

# ── Output spec ───────────────────────────────────────────────
_W, _H         = 1280, 720
_THUMB_MAX     = 200 * 1024      # 200 KB — Telegram thumb= hard limit
_COVER_MAX     = 5 * 1024 * 1024 # 5 MB  — cover= can be full quality
_MIN_BYTES     = 8_000

# ── Cache dir ─────────────────────────────────────────────────
_BASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)

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
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _rm(p):
    try:
        os.remove(p)
    except Exception:
        pass


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


def _save_jpeg_hq(img, dest: str) -> bool:
    """Save PIL image as high-quality JPEG for cover= (up to 5 MB)."""
    try:
        img.save(dest, "JPEG", quality=95, subsampling=0, optimize=True)
        if os.path.getsize(dest) <= _COVER_MAX:
            return True
        # If somehow over 5 MB, compress a bit
        img.save(dest, "JPEG", quality=88, subsampling=0, optimize=True)
        return True
    except Exception as e:
        LOGGER.debug(f"_save_jpeg_hq: {e}")
        return False


def _save_jpeg(img, dest: str) -> bool:
    """Save PIL image as JPEG ≤ 200 KB (for thumb= small preview)."""
    try:
        for q in (92, 82, 72, 60, 50):
            img.save(dest, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(dest) <= _THUMB_MAX:
                return True
        return True
    except Exception as e:
        LOGGER.debug(f"_save_jpeg: {e}")
        return False


def _landscape_crop(img, w=_W, h=_H):
    """Centre-crop an image to exactly w×h, scaling to fill first."""
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


async def _download_raw(session, url, dest) -> bool:
    """Download image to dest as-is (no PIL conversion — keeps original quality)."""
    data = await _get_bytes(session, url)
    if not data:
        return False
    try:
        async with aiofiles.open(dest, "wb") as f:
            await f.write(data)
        return True
    except Exception:
        return False


async def _download(session, url, dest) -> bool:
    """Download image and convert to JPEG at high quality."""
    data = await _get_bytes(session, url)
    if not data:
        return False
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return _save_jpeg_hq(img, dest)
    except Exception:
        try:
            async with aiofiles.open(dest, "wb") as f:
                await f.write(data)
            return True
        except Exception:
            return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  COMPOSITOR — logo PNG + backdrop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _composite(bg_path: str, logo_bytes: bytes, dest: str, title: str = "") -> bool:
    """
    Composite a transparent PNG logo onto a backdrop.

    Layout:
      - Backdrop fills 1280×720 (letterbox-cropped)
      - Soft dark gradient covers bottom 55% for contrast
      - Logo placed bottom-left, max 520px wide × 200px tall
      - Logo brightness-normalized so it's always visible
      - Drop shadow underneath logo
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

        W, H = _W, _H

        # Background
        bg     = Image.open(bg_path).convert("RGB")
        canvas = _landscape_crop(bg, W, H).convert("RGBA")

        # Cinematic gradient (bottom 60%)
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(H):
            t     = max(0.0, (y - H * 0.35) / (H * 0.65))
            alpha = int(220 * (t ** 1.3))
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad)

        # Logo
        logo   = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        lw, lh = logo.size

        # Scale: max 520px wide, max 200px tall, never upscale
        max_lw, max_lh = 520, 200
        sc   = min(max_lw / lw, max_lh / lh, 1.0)
        lw   = max(int(lw * sc), 1)
        lh   = max(int(lh * sc), 1)
        logo = logo.resize((lw, lh), Image.LANCZOS)

        # Brightness normalize
        r, g, b, a = logo.split()
        rgb = Image.merge("RGB", (r, g, b))
        rgb = ImageEnhance.Brightness(rgb).enhance(1.2)
        r, g, b = rgb.split()
        logo = Image.merge("RGBA", (r, g, b, a))

        # Position: bottom-left with padding
        pad = 56
        lx  = pad
        ly  = H - lh - pad

        # Drop shadow
        shadow = Image.new("RGBA", (lw + 28, lh + 28), (0, 0, 0, 0))
        mask   = a.point(lambda p: int(p * 0.55))
        black  = Image.new("RGB", (lw, lh), (0, 0, 0))
        sh_img = Image.merge("RGBA", (*black.split(), mask))
        shadow.paste(sh_img, (14, 14))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=12))
        canvas.alpha_composite(shadow, dest=(max(0, lx - 10), max(0, ly - 10)))
        canvas.alpha_composite(logo, dest=(lx, ly))

        final = canvas.convert("RGB")
        return _save_jpeg_hq(final, dest)

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

        grad = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(260):
            alpha = int(235 * (y / 259) ** 1.2)
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad, dest=(0, H - 260))

        draw  = ImageDraw.Draw(canvas)
        font  = _font(_BOLD, 68)
        words = title.upper().split()
        lines, cur = [], ""
        for w in words:
            test = f"{cur} {w}".strip()
            try:
                bw = draw.textbbox((0, 0), test, font=font)[2]
            except Exception:
                bw = len(test) * 32
            if bw <= W - 130:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)

        lh = 78
        ty = H - 50 - len(lines) * lh
        for line in lines:
            try:
                tw = draw.textbbox((0, 0), line, font=font)[2]
            except Exception:
                tw = len(line) * 34
            lx = (W - tw) // 2
            draw.text((lx + 3, ty + 3), line, font=font, fill=(0, 0, 0, 180))
            draw.text((lx, ty),         line, font=font, fill=(255, 255, 255, 255))
            ty += lh

        return _save_jpeg_hq(canvas.convert("RGB"), dest)
    except Exception as e:
        LOGGER.debug(f"_text_overlay: {e}")
        try:
            import shutil
            shutil.copy2(bg_path, dest)
            return True
        except Exception:
            return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CINEMA LANDSCAPE from PORTRAIT POSTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _poster_to_landscape(src: str, out: str, title: str = "") -> bool:
    """
    Convert an actual movie poster (portrait) to 1280×720 cinematic landscape.

    The POSTER IS THE HERO — placed CENTER STAGE at full height.
    The actual movie title artwork baked into the poster is fully visible.

    Layout:
      - Poster fills the FULL height of the canvas, centered horizontally
      - Both sides: blurred, colour-shifted version of the same poster
        (stretched to fill) — seamlessly extends the poster's colour palette
      - Soft vignette edges blend the sides into the center poster
      - NO extra text drawn — the poster's own title art is the title
      - Thin cinematic letterbox bars (top/bottom) if poster is very wide
      - Soft drop shadow around the poster for depth

    Result: looks like a real movie banner — the poster artwork, including
    its logo/title text, is the dominant visual at maximum readable size.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

        W, H = _W, _H
        img  = Image.open(src).convert("RGB")
        iw, ih = img.size

        # ── Scale poster to fill full canvas height ────────────
        # Poster gets as tall as the canvas — title text in poster is LARGE
        scale   = H / ih
        fw, fh  = int(iw * scale), int(ih * scale)

        # If the scaled poster is wider than the canvas, scale by width instead
        # (handles unusually wide posters)
        if fw > W:
            scale = W / iw
            fw, fh = int(iw * scale), int(ih * scale)

        poster_main = img.resize((fw, fh), Image.LANCZOS)

        # ── Background: blurred stretched poster fills both sides ──
        # Use the same poster stretched wide — keeps the colour palette consistent
        bg_scale = max(W / iw, H / ih) * 1.05   # slightly oversized to avoid edge artifacts
        bg = img.resize((int(iw * bg_scale), int(ih * bg_scale)), Image.LANCZOS)
        bw, bh = bg.size
        # Center crop to exactly W×H
        bx = (bw - W) // 2
        by = (bh - H) // 2
        bg = bg.crop((bx, by, bx + W, by + H))
        # Heavy blur + strong darken so the sides don't compete with the poster
        bg = bg.filter(ImageFilter.GaussianBlur(radius=32))
        bg = ImageEnhance.Brightness(bg).enhance(0.28)
        # Slight colour desaturate so blurred sides look cinematic, not distracting
        bg_grey  = bg.convert("L").convert("RGB")
        bg = Image.blend(bg, bg_grey, alpha=0.4)

        canvas = bg.convert("RGBA")

        # ── Center the poster on canvas ────────────────────────
        px = (W - fw) // 2
        py = (H - fh) // 2

        # Soft drop shadow (rendered before poster so it's behind it)
        shadow_pad = 24
        shadow = Image.new("RGBA", (fw + shadow_pad * 2, fh + shadow_pad * 2), (0, 0, 0, 0))
        sb     = Image.new("RGBA", (fw, fh), (0, 0, 0, 180))
        shadow.paste(sb, (shadow_pad, shadow_pad))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=22))
        sx = px - shadow_pad
        sy = py - shadow_pad
        canvas.alpha_composite(shadow, dest=(max(0, sx), max(0, sy)))

        # ── Paste the real poster — CENTERED, FULL HEIGHT ──────
        canvas.alpha_composite(poster_main.convert("RGBA"), dest=(px, py))

        # ── Vignette: soft dark edges left & right to frame poster ─
        # Fade from dark (edges) to transparent (where poster is)
        vign_w = max(px + 40, 80)   # covers the blurred side + bleeds slightly over poster edge
        for side_x, direction in ((0, 1), (W, -1)):
            vign = Image.new("RGBA", (vign_w, H), (0, 0, 0, 0))
            gd   = ImageDraw.Draw(vign)
            for x in range(vign_w):
                t     = 1.0 - (x / vign_w) ** 0.6   # aggressive at edge, soft toward center
                alpha = int(200 * t)
                gd.line([(x, 0), (x, H)], fill=(0, 0, 0, alpha))
            if direction == 1:
                canvas.alpha_composite(vign, dest=(0, 0))
            else:
                canvas.alpha_composite(vign.transpose(Image.FLIP_LEFT_RIGHT),
                                       dest=(W - vign_w, 0))

        return _save_jpeg_hq(canvas.convert("RGB"), out)
    except Exception as e:
        LOGGER.debug(f"_poster_to_landscape: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  METAHUB  (transparent PNG logo by IMDb ID)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _metahub_logo(session, imdb_id: str) -> bytes | None:
    """
    Fetch a transparent PNG logo from metahub.space using the IMDb ID.
    Returns raw PNG bytes on success, None otherwise.

    Only called when Fanart.tv and TMDB both failed to supply a logo,
    so there is no risk of double-compositing an existing logo.
    """
    if not imdb_id:
        return None
    url = _METAHUB.format(imdb_id=imdb_id)
    data = await _get_bytes(session, url)
    if not data:
        return None
    # Sanity-check: must be a real PNG (at least 1 KB, starts with PNG magic)
    if len(data) < 1024 or data[:4] != b"\x89PNG":
        return None
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TMDB
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _tmdb_search(session, title: str, year) -> tuple[int | None, str]:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key:
        return None, "movie"
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
    """
    Fetch thumbnail from TMDB.
    Priority:
      1. Textless backdrop + Logo PNG composite  ← only iso_639_1=null backdrops
         to prevent double-logo when a backdrop already has the title baked in.
         Logo sources tried in order: TMDB logos → Metahub
      2. Actual movie poster → cinematic landscape (real poster with movie title art)
      3. Any backdrop + text overlay (last resort — draws plain text, no logo PNG)
    """
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key:
        return False

    data = await _get_json(session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
                           {"api_key": key,
                            "include_image_language": "en,hi,te,ta,null"})

    def _sort(lst):
        return sorted(lst,
                      key=lambda x: (float(x.get("vote_average", 0)),
                                     int(x.get("vote_count", 0))),
                      reverse=True)

    all_backdrops = _sort(data.get("backdrops", []))
    logos         = _sort([l for l in data.get("logos", [])
                           if l.get("file_path", "").endswith(".png")])
    posters       = _sort(data.get("posters", []))

    # ── Textless backdrops only — no baked-in title art ──────
    # iso_639_1 == null means the backdrop has NO language overlay / title text.
    # Using a language backdrop (e.g. "en") with a logo composited on top
    # would produce a DOUBLE LOGO (one baked-in + one composited).
    textless_backdrops = [b for b in all_backdrops
                          if not b.get("iso_639_1")]

    bg_tmp     = dest + ".tm_bg.tmp"
    poster_tmp = dest + ".tm_poster.tmp"

    # ── Strategy 1: Textless backdrop + Logo PNG (no double logo) ─
    # When all logo sources fail, falls back to text overlay on the SAME
    # textless backdrop rather than wasting it and re-downloading in strategy 3.
    best_textless_bg = None   # save the first good textless backdrop path for fallback
    if textless_backdrops:
        # Get IMDb ID now so Metahub is available as logo fallback
        ext     = await _external_ids(session, tmdb_id, mtype)
        imdb_id = ext.get("imdb_id", "")

        # Pre-fetch Metahub logo once — same logo would be tried for every backdrop
        mh_bytes = await _metahub_logo(session, imdb_id) if imdb_id else None

        for bd in textless_backdrops[:5]:
            fp = bd.get("file_path", "")
            if not fp:
                continue
            if not await _download(session, _W1280 + fp, bg_tmp):
                continue
            if not _ok(bg_tmp):
                _rm(bg_tmp)
                continue

            # Try TMDB logos
            for logo in logos[:5]:
                lfp    = logo.get("file_path", "")
                lbytes = await _get_bytes(session, _ORIG + lfp) if lfp else None
                if lbytes and _composite(bg_tmp, lbytes, dest, title):
                    _rm(bg_tmp)
                    LOGGER.info("[Thumb] ✅ TMDB: textless backdrop + TMDB logo")
                    return True

            # TMDB logos failed — try Metahub logo
            if mh_bytes and _composite(bg_tmp, mh_bytes, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ TMDB: textless backdrop + Metahub logo")
                return True

            # All logo sources failed — use this textless backdrop with text overlay
            # immediately rather than deleting it and re-downloading in strategy 3.
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ TMDB: textless backdrop + text overlay (no logo found)")
                return True

            _rm(bg_tmp)

    # ── Strategy 2: Actual Movie Poster → landscape ───────────
    # The REAL movie poster already has the actual movie logo text baked in.
    # No additional logo is composited here — zero risk of double logo.
    if posters:
        for poster in posters[:4]:
            fp = poster.get("file_path", "")
            if not fp:
                continue
            if not await _download_raw(session, _ORIG + fp, poster_tmp):
                if not await _download_raw(session, _W780 + fp, poster_tmp):
                    continue
            if not _ok(poster_tmp):
                _rm(poster_tmp)
                continue

            if _poster_to_landscape(poster_tmp, dest, title):
                _rm(poster_tmp)
                LOGGER.info("[Thumb] ✅ TMDB: actual movie poster → landscape")
                return True
            _rm(poster_tmp)

    # ── Strategy 3: Any backdrop + text overlay (last resort) ──
    # Only draw plain text — the backdrop may already have a title baked in,
    # but _text_overlay draws text only when no logo PNG is available, so
    # double-logo cannot happen here (text ≠ PNG logo composite).
    for bd in all_backdrops[:4]:
        fp = bd.get("file_path", "")
        if not fp:
            continue
        if not await _download(session, _W1280 + fp, bg_tmp):
            continue
        if not _ok(bg_tmp):
            _rm(bg_tmp)
            continue
        if _text_overlay(bg_tmp, dest, title):
            _rm(bg_tmp)
            LOGGER.info("[Thumb] ✅ TMDB: backdrop + text overlay")
            return True
        _rm(bg_tmp)

    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FANART.TV
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _fanart_thumb(session, tmdb_id, mtype, dest, title) -> bool:
    fa_key = getattr(config, "FANART_API_KEY", "").strip()
    if not fa_key:
        return False

    ext = await _external_ids(session, tmdb_id, mtype)

    if mtype == "movie":
        fid          = ext.get("imdb_id", "")
        base_url     = _FANART_MOVIE
        logo_keys    = ["hdmovielogo", "movielogo"]
        # moviebackground = clean backdrop (no logo baked in) — safe for compositing
        # moviethumb      = pre-composited backdrop+logo — use as-is, never composite onto
        clean_bg_keys   = ["moviebackground"]
        precomp_keys    = ["moviethumb"]
    else:
        fid          = str(ext.get("tvdb_id", ""))
        base_url     = _FANART_TV
        logo_keys    = ["hdtvlogo", "tvlogo", "clearlogo"]
        clean_bg_keys   = ["showbackground"]
        precomp_keys    = ["tvthumb"]

    if not fid:
        LOGGER.debug(f"[Thumb][Fanart] no external ID tmdb={tmdb_id}")
        return False

    data = await _get_json(session, f"{base_url}/{fid}", {"api_key": fa_key})
    if not data:
        LOGGER.debug(f"[Thumb][Fanart] no data for {fid}")
        return False

    def _top(key, n=5):
        items = data.get(key, [])
        return sorted(items, key=lambda x: int(x.get("likes", 0)), reverse=True)[:n]

    logos = []
    for k in logo_keys:
        logos.extend(_top(k, 4))

    # Clean backgrounds only — these have NO baked-in logo/title text.
    # Never use moviethumb here: it already has a logo composited in it.
    clean_bgs = []
    for k in clean_bg_keys:
        clean_bgs.extend(_top(k, 4))

    precomp = []
    for k in precomp_keys:
        precomp.extend(_top(k, 3))

    bg_tmp = dest + ".fa_bg.tmp"

    # ── Strategy 1: Clean backdrop + logo composite ───────────
    # Only use moviebackground (textless) + logo PNG.
    # moviethumb is intentionally excluded — it already has the logo baked in.
    # If no logo is found (Fanart + Metahub both fail), falls back to text
    # overlay on the same clean backdrop immediately (no wasted re-download).
    if clean_bgs:
        imdb_id  = ext.get("imdb_id", "")
        # Pre-fetch Metahub logo once — same logo for every backdrop attempt
        mh_bytes = await _metahub_logo(session, imdb_id) if imdb_id else None

        for bg_art in clean_bgs[:4]:
            url = bg_art.get("url", "")
            if not url:
                continue
            if not await _download(session, url, bg_tmp):
                continue
            if not _ok(bg_tmp):
                _rm(bg_tmp)
                continue

            # Try Fanart logos
            for logo_art in logos[:4]:
                lurl   = logo_art.get("url", "")
                lbytes = await _get_bytes(session, lurl) if lurl else None
                if lbytes and _composite(bg_tmp, lbytes, dest, title):
                    _rm(bg_tmp)
                    LOGGER.info("[Thumb] ✅ Fanart: clean backdrop + logo")
                    return True

            # Fanart logos failed — try Metahub logo
            if mh_bytes and _composite(bg_tmp, mh_bytes, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: clean backdrop + Metahub logo")
                return True

            # All logo sources failed — text overlay on the clean backdrop
            # immediately rather than wasting it and re-downloading in strategy 3.
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: clean backdrop + text (no logo found)")
                return True
            _rm(bg_tmp)

    # ── Strategy 2: Pre-composited moviethumb — use as-is ────
    # These already have backdrop + logo merged by Fanart.tv artists.
    # DO NOT composite any additional logo onto them.
    for art in precomp[:3]:
        url = art.get("url", "")
        if not url:
            continue
        if await _download(session, url, dest) and _ok(dest):
            try:
                from PIL import Image
                img = Image.open(dest)
                if img.size != (_W, _H):
                    img = _landscape_crop(img.convert("RGB"))
                    _save_jpeg_hq(img, dest)
            except Exception:
                pass
            LOGGER.info("[Thumb] ✅ Fanart: moviethumb (pre-composited)")
            return True

    # ── Strategy 3: Clean background + text (no logos available) ─
    for bg_art in clean_bgs[:3]:
        url = bg_art.get("url", "")
        if not url:
            continue
        if await _download(session, url, bg_tmp) and _ok(bg_tmp):
            if _text_overlay(bg_tmp, dest, title):
                _rm(bg_tmp)
                LOGGER.info("[Thumb] ✅ Fanart: clean bg + text fallback")
                return True
            _rm(bg_tmp)

    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  OMDB — real poster URL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _omdb_thumb(session, title: str, year, dest: str) -> bool:
    """
    Fetch the actual movie poster from OMDB (which uses high-res IMDb posters).
    These are the REAL movie posters with actual logo artwork.
    """
    key = getattr(config, "OMDB_API_KEY", "").strip()
    if not key:
        return False

    params = {"apikey": key, "t": title, "type": "movie"}
    if year:
        params["y"] = year

    data = await _get_json(session, _OMDB, params)
    poster_url = data.get("Poster", "")
    if not poster_url or poster_url == "N/A":
        # Try TV
        params["type"] = "series"
        data = await _get_json(session, _OMDB, params)
        poster_url = data.get("Poster", "")

    if not poster_url or poster_url == "N/A":
        return False

    # OMDB gives ~300px poster URLs — try to get full size
    # Pattern: https://m.media-amazon.com/images/M/...._V1_SX300.jpg
    # Replace SX300 with SX1000 for higher resolution
    hd_url = re.sub(r"_SX\d+", "_SX1000", poster_url)
    hd_url = re.sub(r"_SY\d+", "_SY1000", hd_url)

    tmp = dest + ".omdb_tmp.jpg"
    ok  = await _download_raw(session, hd_url, tmp)
    if not ok:
        ok = await _download_raw(session, poster_url, tmp)
    if not ok or not _ok(tmp):
        _rm(tmp)
        return False

    if _poster_to_landscape(tmp, dest, title):
        _rm(tmp)
        LOGGER.info("[Thumb] ✅ OMDB: real poster → landscape")
        return True

    _rm(tmp)
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ITUNES  (portrait poster → landscape)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _itunes_thumb(session, title: str, mtype: str, dest: str) -> bool:
    entity = "movie" if mtype == "movie" else "tvShow"
    for country in ("us", "in", "gb"):
        data = await _get_json(session, "https://itunes.apple.com/search", {
            "term":    title,
            "media":   "movie" if mtype == "movie" else "tvShow",
            "entity":  entity,
            "limit":   "8",
            "country": country,
        })
        for item in data.get("results", [])[:8]:
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if not art:
                continue
            # Get the highest resolution
            hd  = re.sub(r"/\d+x\d+bb/", "/3000x3000bb/", art)
            tmp = dest + ".it_tmp.jpg"
            if await _download_raw(session, hd, tmp) and _ok(tmp):
                ok = _poster_to_landscape(tmp, dest, title)
                _rm(tmp)
                if ok:
                    LOGGER.info("[Thumb] ✅ iTunes → landscape")
                    return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FFMPEG FRAME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _ffmpeg_frame(video: str, dest: str) -> bool:
    if not video or not os.path.exists(video):
        return False
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
    except Exception:
        dur = 0

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

        # Deep cinematic gradient
        for y in range(H):
            t = y / H
            draw.line([(0, y), (W, y)],
                      fill=(int(6 + (2 - 6) * t), int(12 + (4 - 12) * t),
                            int(32 + (10 - 32) * t), 255))

        # Film grain
        for _ in range(14000):
            x, y = rng.randint(0, W-1), rng.randint(0, H-1)
            br   = rng.randint(12, 36)
            draw.point((x, y), fill=(br, br, br, rng.randint(25, 60)))

        # Gold accent lines
        acc = (210, 160, 30)
        for yp in [H // 2 - 96, H // 2 + 96]:
            draw.line([(100, yp), (W - 100, yp)], fill=(*acc, 145), width=1)

        # Corner marks
        for cx, cy, dx, dy in [(72, 72, 1, 1), (W-72, 72, -1, 1),
                                (72, H-72, 1, -1), (W-72, H-72, -1, -1)]:
            draw.line([(cx, cy), (cx+dx*40, cy)], fill=(*acc, 150), width=2)
            draw.line([(cx, cy), (cx, cy+dy*40)], fill=(*acc, 150), width=2)

        # Watermark
        wm = getattr(config, "WATERMARK", "NXT HUB")
        draw.text((82, 54), f"⚡ {wm}", font=_font(_BOLD, 20), fill=(210, 160, 30, 200))

        # Title
        tfont = _font(_BOLD, 76)
        words = title.upper().split()
        lines, cur = [], ""
        for w in words:
            test = f"{cur} {w}".strip()
            try:
                tw = draw.textbbox((0, 0), test, font=tfont)[2]
            except Exception:
                tw = len(test) * 36
            if tw <= W - 160:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)

        lh = 88
        ty = (H - len(lines) * lh) // 2 - 20
        for line in lines:
            try:
                lw = draw.textbbox((0, 0), line, font=tfont)[2]
            except Exception:
                lw = len(line) * 38
            lx = (W - lw) // 2
            for off, al in [(4, 50), (2, 90), (1, 125)]:
                draw.text((lx+off, ty+off), line, font=tfont, fill=(30, 60, 120, al))
            draw.text((lx, ty), line, font=tfont, fill=(255, 255, 255, 255))
            ty += lh

        sub = "  ·  ".join(p for p in [year, genre] if p)
        if sub:
            sf  = _font(_REG, 28)
            try:
                sw = draw.textbbox((0, 0), sub, font=sf)[2]
            except Exception:
                sw = len(sub) * 14
            draw.text(((W-sw)//2, ty+10), sub, font=sf, fill=(175, 175, 195, 200))

        return _save_jpeg_hq(canvas.convert("RGB"), dest)
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
        r"|Multi|S\d{2}E\d{2}|S\d{2}|E\d{2})\\b.*",
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
    Fetch or generate a 1280×720 landscape thumbnail with actual movie artwork.

    Priority:
      1. Fanart.tv: real logo PNG + backdrop composite
      2. TMDB: backdrop + logo PNG composite
      3. TMDB: actual movie poster → cinematic landscape (REAL poster art!)
      4. OMDB: real IMDb poster → cinematic landscape
      5. Fanart: pre-composited moviethumb
      6. iTunes portrait poster → landscape
      7. ffmpeg video frame
      8. Title card (always succeeds)

    All thumbnails saved at HIGH QUALITY (up to 5 MB) for cover= parameter.
    thumb= parameter uses prep_for_upload() to compress to 200 KB / 320×320.
    """
    label = title_overlay or title
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    # Check cache
    cached = _cache_get(title, year)
    if cached:
        try:
            import shutil
            shutil.copy2(cached, dest)
            return True
        except Exception:
            pass

    LOGGER.info(f"[Thumb] Fetching: '{title}' ({year})")

    async with aiohttp.ClientSession() as s:
        tmdb_id, mtype = await _tmdb_search(s, title, year)

        if tmdb_id:
            LOGGER.info(f"[Thumb] TMDB match: id={tmdb_id} type={mtype}")

            # 1. Fanart: real logo + backdrop (highest quality)
            if await _fanart_thumb(s, tmdb_id, mtype, dest, label):
                if _ok(dest):
                    _cache_put(title, year, dest)
                    return True

            # 2 & 3. TMDB: backdrop+logo OR actual movie poster → landscape
            if await _tmdb_thumb(s, tmdb_id, mtype, dest, label):
                if _ok(dest):
                    _cache_put(title, year, dest)
                    return True

        # 4. OMDB: real IMDb poster → cinematic landscape
        if await _omdb_thumb(s, title, year, dest):
            if _ok(dest):
                _cache_put(title, year, dest)
                return True

        # 5. iTunes portrait → landscape
        mt = mtype if tmdb_id else "movie"
        if await _itunes_thumb(s, title, mt, dest):
            if _ok(dest):
                _cache_put(title, year, dest)
                return True

    # 6. ffmpeg frame (real video frame — no fake poster)
    if video_path and await _ffmpeg_frame(video_path, dest):
        if _ok(dest):
            return True

    # No real poster found — do NOT generate a fake title card
    LOGGER.info(f"[Thumb] ⚠️ No real poster found for '{title}' — skipping thumbnail")
    return False


async def generate_hd_thumb(
    file_path: str,
    uid: int = 0,
    custom_thumb: str | None = None,
) -> str | None:
    """
    Main entry point for the uploader.
    Returns path to a 1280×720 HIGH QUALITY JPEG thumbnail, or None if no
    real poster was found.

    The returned image is used for BOTH:
      - cover= parameter (sent as-is, high quality)
      - thumb= parameter (compressed to 320×320 / 200 KB via prep_for_upload)

    Priority:
      1. Explicit custom_thumb passed by caller
      2. User's saved custom thumbnail (from /settings)
      3. Fanart.tv: real logo + backdrop (actual movie artwork)
      4. TMDB: backdrop+logo OR actual movie poster → landscape
      5. OMDB: real IMDb poster → landscape
      6. iTunes portrait poster → landscape
      7. ffmpeg video frame
      8. None — NO fake/custom title card is generated
    """
    from bot.utils.thumb_store import TMP_DIR as tmp
    os.makedirs(tmp, exist_ok=True)

    # 1. Explicit override
    if custom_thumb and os.path.exists(custom_thumb):
        dest = os.path.join(tmp, f"custom_{int(time.time())}.jpg")
        try:
            from PIL import Image
            img = _landscape_crop(Image.open(custom_thumb).convert("RGB"))
            _save_jpeg_hq(img, dest)
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
                _save_jpeg_hq(img, dest)
                return dest
        except Exception:
            pass

    # 3–8. Auto-fetch from APIs
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


# prep_thumb is imported from thumb_store
def prep_thumb(src: str, dest: str | None = None) -> str | None:
    from bot.utils.thumb_store import prep_for_upload
    return prep_for_upload(src, dest)
