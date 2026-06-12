"""
thumbnail.py — HD Landscape Thumbnail Fetcher

Priority (sequential — stops as soon as one source succeeds):
  1. Fanart.tv  moviebackground/showbackground sorted by likes (best popularity first)
  2. TMDB /images — best vote_average backdrop at /original
  3. TMDB main   — backdrop_path at /original
  4. iTunes      — HD artwork (great for Indian/regional content)
  5. Portrait fallback — any portrait converted to landscape via PIL
     using the high-quality blurred BG + centered poster method
     (ported from reference image.py — drop shadow, gradient, title text)
"""

import os
import re
import asyncio
import aiohttp
import config

_TMDB         = "https://api.themoviedb.org/3"
_ORIG         = "https://image.tmdb.org/t/p/original"   # full resolution — up to 4K
_FANART_MOVIE = "https://webservice.fanart.tv/v3/movies"
_FANART_TV    = "https://webservice.fanart.tv/v3/tv"
_HEADERS      = {
    "User-Agent":      "Mozilla/5.0 (compatible; NXTHubBot/4.0)",
    "Accept":          "image/webp,image/jpeg,image/*,*/*",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT      = aiohttp.ClientTimeout(total=60, connect=10)  # 60s for large HD images
_MIN_BYTES    = 5_000    # 5KB minimum — avoids broken images but allows compressed JPEGs
_SEARCH_LANGS = ["en-US", "hi-IN", "te-IN", "ta-IN", "ml-IN", "bn-IN"]

# Font paths from reference image.py
_FONT_BOLD   = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
_FONT_MEDIUM = "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf"


# ── HTTP helpers ──────────────────────────────────────────────

async def _get(session, url, params=None):
    try:
        async with session.get(url, params=params, headers=_HEADERS,
                               timeout=_TIMEOUT, allow_redirects=True) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


async def _dl(session, url, dest):
    """Download image to dest. Converts WebP → JPEG for Telegram compatibility."""
    try:
        async with session.get(url, headers=_HEADERS, timeout=_TIMEOUT,
                               allow_redirects=True) as r:
            if r.status != 200:
                return False
            data = await r.read()
            if len(data) < _MIN_BYTES:
                return False

            # Check if response is WebP — convert to JPEG so Telegram renders it
            content_type = r.headers.get("Content-Type", "")
            is_webp = "webp" in content_type.lower() or data[:4] == b"RIFF"

            if is_webp:
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(data)).convert("RGB")
                    img.save(dest, "JPEG", quality=95, subsampling=0)
                    return True
                except Exception:
                    pass   # fall through to raw write

            # Non-WebP: re-save via PIL to enforce q=95, subsampling=0
            # (avoids passing a pre-compressed JPEG with unknown quality to Telegram)
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(data)).convert("RGB")
                img.save(dest, "JPEG", quality=95, subsampling=0, optimize=True)
                return True
            except Exception:
                pass

            # PIL not available — write raw bytes
            with open(dest, "wb") as f:
                f.write(data)
            return True
    except Exception:
        return False


# ── TMDB multi-language search ────────────────────────────────

async def _tmdb_search(session, title, year):
    if not config.TMDB_API_KEY:
        return None, "movie"
    for lang in _SEARCH_LANGS:
        for mtype in ("movie", "tv"):
            p = {"api_key": config.TMDB_API_KEY, "query": title,
                 "language": lang, "include_adult": "false"}
            if year:
                p["year" if mtype == "movie" else "first_air_date_year"] = year
            data = await _get(session, f"{_TMDB}/search/{mtype}", p)
            results = [r for r in data.get("results", []) if r.get("id")]
            if results:
                return results[0]["id"], mtype
    return None, "movie"


async def _external_ids(session, tmdb_id, mtype):
    return await _get(session, f"{_TMDB}/{mtype}/{tmdb_id}/external_ids",
                      {"api_key": config.TMDB_API_KEY})


# ── Source 1: Fanart.tv — sorted by likes (popularity first) ─

async def _fanart(session, tmdb_id, mtype, dest):
    """
    Fetches Fanart landscape images sorted by likes descending.

    Movie priority (all landscape, best quality first):
      1. moviebackground — 1920×1080 strict landscape backdrop  ← BEST
      2. moviethumb      — 1000×562 landscape with logo/chars
      3. moviebanner     — 1000×185 wide banner (last resort)

    TV priority:
      1. showbackground  — 1920×1080 strict landscape backdrop  ← BEST
      2. tvthumb         — 1000×562 landscape with logo/chars
      3. tvbanner        — wide banner (last resort)

    Within each type, sorted by likes descending so most community-voted
    popular image is tried first. Stops as soon as one downloads.
    """
    if not getattr(config, "FANART_API_KEY", ""):
        return False

    ext = await _external_ids(session, tmdb_id, mtype)
    fid = ext.get("imdb_id") if mtype == "movie" else ext.get("tvdb_id")
    if not fid:
        return False

    base = _FANART_MOVIE if mtype == "movie" else _FANART_TV
    data = await _get(session, f"{base}/{fid}",
                      {"api_key": config.FANART_API_KEY})
    if not data:
        return False

    # Priority order — landscape only, best resolution first
    # moviebackground/showbackground = 1920×1080 (highest quality)
    # moviethumb/tvthumb             = 1000×562  (with logo overlay)
    # moviebanner/tvbanner           = 1000×185  (wide but short, last resort)
    keys = (
        ["moviethumb", "moviebackground", "moviebanner"]
        if mtype == "movie" else
        ["tvthumb", "showbackground", "tvbanner"]
    )

    for key in keys:
        arts = data.get(key, [])
        if not arts:
            continue
        # Sort by likes descending — most popular image first
        arts = sorted(arts, key=lambda x: int(x.get("likes", 0)), reverse=True)
        for art in arts[:8]:   # try up to 8 per type before moving to next
            url = art.get("url", "")
            if url and await _dl(session, url, dest):
                return True

    return False


# ── Source 2: TMDB /images — best voted backdrop ─────────────

async def _tmdb_images(session, tmdb_id, mtype, dest):
    if not config.TMDB_API_KEY:
        return False
    data = await _get(
        session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
        {"api_key": config.TMDB_API_KEY, "include_image_language": "en,hi,te,ta,ml,null"},
    )
    backdrops = sorted(
        data.get("backdrops", []),
        key=lambda x: (float(x.get("vote_average", 0)),
                       int(x.get("vote_count", 0))),
        reverse=True,
    )
    for bd in backdrops[:8]:
        path = bd.get("file_path", "")
        if path and await _dl(session, _ORIG + path, dest):
            return True
    return False


# ── Source 3: TMDB main backdrop_path ────────────────────────

async def _tmdb_main(session, tmdb_id, mtype, dest):
    if not config.TMDB_API_KEY:
        return False
    data = await _get(session, f"{_TMDB}/{mtype}/{tmdb_id}",
                      {"api_key": config.TMDB_API_KEY})
    path = data.get("backdrop_path")
    if path:
        return await _dl(session, _ORIG + path, dest)
    return False


# ── Source 4: iTunes (portrait) → convert to landscape ───────

async def _itunes(session, title, mtype, dest, overlay=""):
    entity = "movie" if mtype == "movie" else "tvShow"
    for country in ("in", "us"):
        data = await _get(session, "https://itunes.apple.com/search",
                          {"term": title, "media": "movie" if mtype == "movie" else "tvShow",
                           "entity": entity, "limit": "6", "country": country})
        for item in data.get("results", [])[:6]:
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if not art:
                continue
            hd  = re.sub(r"/\d+x\d+bb/", "/2000x2000bb/", art)
            tmp = dest + ".itunes.tmp.jpg"
            if await _dl(session, hd, tmp):
                ok = _portrait_to_landscape(tmp, dest, overlay or title)
                try: os.remove(tmp)
                except Exception: pass
                if ok:
                    return True
    return False


# ── Portrait → Landscape (high-quality PIL, from reference image.py) ──────────

def _load_font(path, size):
    try:
        from PIL import ImageFont
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            from PIL import ImageFont
            return ImageFont.load_default()
        except Exception:
            return None


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        try:
            bbox = draw.textbbox((0, 0), test, font=font)
            w = bbox[2] - bbox[0]
        except Exception:
            w = len(test) * 10
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _portrait_to_landscape(input_path: str, output_path: str, title: str = "") -> bool:
    """
    High-quality portrait → 1280×720 landscape conversion.
    Ported from reference image.py:
      - Blurred + darkened background fill (Gaussian radius 22)
      - Sharp poster centered with drop shadow
      - Optional title text with gradient bar at bottom
    Falls back to ffmpeg if PIL unavailable.
    """
    try:
        from PIL import Image, ImageFilter, ImageDraw
        W, H = 1280, 720

        img = Image.open(input_path).convert("RGB")
        iw, ih = img.size

        # ── Background: fill → blur → darken ─────────────────
        bg_sc = max(W / iw, H / ih)
        bg    = img.resize((int(iw * bg_sc), int(ih * bg_sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg    = bg.crop(((bw - W) // 2, (bh - H) // 2,
                          (bw - W) // 2 + W, (bh - H) // 2 + H))
        bg    = bg.filter(ImageFilter.GaussianBlur(radius=22))
        dark  = Image.new("RGB", (W, H), (0, 0, 0))
        canvas = Image.blend(bg, dark, alpha=0.55).convert("RGBA")

        # ── Foreground: sharp poster + drop shadow ────────────
        pad_top    = 36
        pad_bottom = 120 if title else 40
        avail_h    = H - pad_top - pad_bottom
        avail_w    = int(W * 0.52)

        fg_sc = min(avail_w / iw, avail_h / ih)
        fw, fh = int(iw * fg_sc), int(ih * fg_sc)
        fg    = img.resize((fw, fh), Image.LANCZOS)

        # Drop shadow (from reference image.py)
        shadow_offset = 8
        shadow_layer  = Image.new("RGBA", (fw + shadow_offset * 2,
                                           fh + shadow_offset * 2), (0, 0, 0, 0))
        _, _, _, alpha_ch = fg.convert("RGBA").split()
        shadow_alpha = alpha_ch.point(lambda p: int(p * 0.55)) if alpha_ch else None
        if shadow_alpha:
            shadow_rgb = Image.new("RGB", (fw, fh), (0, 0, 0))
            r0, g0, b0 = shadow_rgb.split()
            shadow_img = Image.merge("RGBA", (r0, g0, b0, shadow_alpha))
            shadow_layer.paste(shadow_img, (shadow_offset, shadow_offset))
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=10))
            sx = (W - fw) // 2 - shadow_offset
            sy = pad_top + (avail_h - fh) // 2 - shadow_offset
            canvas.alpha_composite(shadow_layer, dest=(max(0, sx), max(0, sy)))

        canvas.paste(fg, ((W - fw) // 2, pad_top + (avail_h - fh) // 2))

        # ── Title text with gradient bar (from reference image.py) ──
        if title:
            grad   = Image.new("RGBA", (W, 160), (0, 0, 0, 0))
            gd     = ImageDraw.Draw(grad)
            for y in range(160):
                alpha = int(220 * (y / 159) ** 1.5)
                gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
            canvas.alpha_composite(grad, dest=(0, H - 160))

            draw       = ImageDraw.Draw(canvas)
            title_font = _load_font(_FONT_BOLD, 52)
            lines      = _wrap_text(draw, title.upper(), title_font, W - 120)
            line_h     = 60
            text_y     = H - 30 - len(lines) * line_h

            for line in lines:
                try:
                    bbox = draw.textbbox((0, 0), line, font=title_font)
                    lw   = bbox[2] - bbox[0]
                except Exception:
                    lw = len(line) * 28
                lx = (W - lw) // 2
                draw.text((lx + 2, text_y + 2), line, font=title_font,
                          fill=(0, 0, 0, 180))
                draw.text((lx, text_y), line, font=title_font,
                          fill=(255, 255, 255, 255))
                text_y += line_h

        canvas.convert("RGB").save(output_path, "JPEG", quality=95, subsampling=0)
        return True

    except Exception:
        # Fallback: ffmpeg
        try:
            import subprocess
            subprocess.run([
                "ffmpeg", "-i", input_path,
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                "-q:v", "2", "-frames:v", "1", "-y", output_path,
            ], capture_output=True, timeout=30)
            return os.path.isfile(output_path)
        except Exception:
            return False


# ── Main public function ──────────────────────────────────────

async def get_thumbnail(title: str, year: str | None, dest: str,
                        title_overlay: str = "") -> bool:
    """
    Fetch HD landscape thumbnail. title_overlay is shown on portrait→landscape
    conversion (movie name on the image). If not given, uses title.
    """
    overlay = title_overlay or title
    async with aiohttp.ClientSession() as session:
        tmdb_id, mtype = await _tmdb_search(session, title, year)
        if tmdb_id:
            if await _fanart(session, tmdb_id, mtype, dest):
                return True
            if await _tmdb_images(session, tmdb_id, mtype, dest):
                return True
            if await _tmdb_main(session, tmdb_id, mtype, dest):
                return True
        mt = mtype if tmdb_id else "movie"
        if await _itunes(session, title, mt, dest, overlay):
            return True
    return False
