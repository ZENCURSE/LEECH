"""
thumbnail.py — HD Auto Thumbnail with Title Logo

Priority:
  1. Fanart.tv  moviethumb — 1000×562 with movie title/logo baked in (BEST)
  2. Fanart.tv  moviebackground — 1920×1080 backdrop → verified backdrop quality
  3. TMDB       /images best-voted backdrop
  4. TMDB       main backdrop_path
  5. iTunes     HD poster → portrait_to_landscape() with title rendered
  6. Built-in   title card — colourful gradient bg + large title text (always works)

Quality checks:
  - Every downloaded image is scored: is it blank/solid colour? Is it too dark?
  - Fanart moviethumb images already have the title logo baked in
  - Backdrop images that pass quality check get title text overlaid at bottom
  - If nothing found, generate_title_card() builds a clean branded card
"""

import os
import re
import asyncio
import aiohttp
import config

_TMDB         = "https://api.themoviedb.org/3"
_ORIG         = "https://image.tmdb.org/t/p/original"
_FANART_MOVIE = "https://webservice.fanart.tv/v3/movies"
_FANART_TV    = "https://webservice.fanart.tv/v3/tv"
_HEADERS      = {
    "User-Agent":      "Mozilla/5.0 (compatible; NXTHubBot/5.0)",
    "Accept":          "image/webp,image/jpeg,image/*,*/*",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT    = aiohttp.ClientTimeout(total=60, connect=10)
_MIN_BYTES  = 10_000   # 10KB minimum
_SEARCH_LANGS = ["en-US", "hi-IN", "te-IN", "ta-IN", "ml-IN"]

# Font — try system fonts, fall back gracefully
_FONTS = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

_FONT_REGULAR = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _best_font(paths: list, size: int):
    from PIL import ImageFont
    for p in paths:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Image quality checker ─────────────────────────────────────

def _is_good_image(path: str) -> bool:
    """
    Returns True if the image is worth using as a thumbnail.
    Rejects only truly broken images:
      - Near-solid colour (std < 8) — blank/error images
      - Completely black (brightness < 5)
      - Completely white/blown out (brightness > 245)
    Dark cinematic images (like RRR, Dark Knight) have std > 15 so they pass.
    We intentionally use loose thresholds — better to show a dark real image
    than a generated title card.
    """
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(path).convert("RGB").resize((160, 90), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32)

        brightness = arr.mean()
        std        = arr.std()

        # Reject only completely black/white/broken
        if brightness < 5:   return False   # completely black
        if brightness > 245: return False   # completely white/blown out
        if std < 8:          return False   # near-solid colour = broken image

        return True
    except Exception:
        return True


def _has_text_region(path: str) -> bool:
    """
    Detects title text/logo in bottom 30% of image using edge bright-pixel count.
    text creates sharp edges — count > 3000 bright pixels confirms text present.
    """
    try:
        import numpy as np
        from PIL import Image, ImageFilter
        img    = Image.open(path).convert("L").resize((320, 180), Image.LANCZOS)
        W, H   = img.size
        bottom = img.crop((0, int(H * 0.70), W, H))
        edge   = bottom.filter(ImageFilter.FIND_EDGES)
        arr    = np.array(edge, dtype=np.float32)
        return int((arr > 30).sum()) > 3000
    except Exception:
        return True


async def _get(session, url, params=None):
    try:
        async with session.get(url, params=params, headers=_HEADERS,
                               timeout=_TIMEOUT, allow_redirects=True) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


async def _dl(session, url, dest) -> bool:
    """Download + convert to JPEG q=95 subsampling=0."""
    try:
        async with session.get(url, headers=_HEADERS, timeout=_TIMEOUT,
                               allow_redirects=True) as r:
            if r.status != 200:
                return False
            data = await r.read()
            if len(data) < _MIN_BYTES:
                return False
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(data)).convert("RGB")
                img.save(dest, "JPEG", quality=95, subsampling=0, optimize=True)
                return True
            except Exception:
                with open(dest, "wb") as f:
                    f.write(data)
                return True
    except Exception:
        return False


# ── TMDB search ───────────────────────────────────────────────

async def _tmdb_search(session, title, year):
    if not getattr(config, "TMDB_API_KEY", ""):
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


async def _tmdb_details(session, tmdb_id, mtype):
    return await _get(session, f"{_TMDB}/{mtype}/{tmdb_id}",
                      {"api_key": config.TMDB_API_KEY})


# ── Source 1: Fanart.tv moviethumb (has title logo baked in) ─

async def _fanart(session, tmdb_id, mtype, dest, title=""):
    if not getattr(config, "FANART_API_KEY", ""):
        return False

    ext = await _external_ids(session, tmdb_id, mtype)
    fid = ext.get("imdb_id") if mtype == "movie" else ext.get("tvdb_id")
    if not fid:
        return False

    base = _FANART_MOVIE if mtype == "movie" else _FANART_TV
    data = await _get(session, f"{base}/{fid}", {"api_key": config.FANART_API_KEY})
    if not data:
        return False

    # Priority: moviethumb/tvthumb (1000×562 WITH title logo) → background
    # moviethumb has the movie title logo baked into the image — best option
    if mtype == "movie":
        priority = ["moviethumb", "moviebackground", "moviebanner"]
    else:
        priority = ["tvthumb", "showbackground", "tvbanner"]

    for key in priority:
        arts = sorted(data.get(key, []),
                      key=lambda x: int(x.get("likes", 0)), reverse=True)
        for art in arts[:6]:
            url = art.get("url", "")
            if not url:
                continue
            tmp = dest + ".ftmp.jpg"
            if await _dl(session, url, tmp):
                if _is_good_image(tmp):
                    # For backdrops without logo, overlay the title
                    if key in ("moviebackground", "showbackground") and title:
                        _overlay_title(tmp, dest, title)
                        try: os.remove(tmp)
                        except Exception: pass
                    else:
                        os.rename(tmp, dest)
                    return True
                try: os.remove(tmp)
                except Exception: pass
    return False


# ── Source 2+3: TMDB backdrops ────────────────────────────────

async def _tmdb_images(session, tmdb_id, mtype, dest, title=""):
    if not getattr(config, "TMDB_API_KEY", ""):
        return False
    data = await _get(
        session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
        {"api_key": config.TMDB_API_KEY,
         "include_image_language": "en,hi,te,ta,ml,null"},
    )
    backdrops = sorted(
        data.get("backdrops", []),
        key=lambda x: (float(x.get("vote_average", 0)),
                       int(x.get("vote_count", 0))),
        reverse=True,
    )
    for bd in backdrops[:8]:
        path = bd.get("file_path", "")
        if not path:
            continue
        tmp = dest + ".ttmp.jpg"
        if await _dl(session, _ORIG + path, tmp):
            if _is_good_image(tmp):
                if title:
                    _overlay_title(tmp, dest, title)
                    try: os.remove(tmp)
                    except Exception: pass
                else:
                    os.rename(tmp, dest)
                return True
            try: os.remove(tmp)
            except Exception: pass
    return False


async def _tmdb_main(session, tmdb_id, mtype, dest, title=""):
    if not getattr(config, "TMDB_API_KEY", ""):
        return False
    data = await _get(session, f"{_TMDB}/{mtype}/{tmdb_id}",
                      {"api_key": config.TMDB_API_KEY})
    path = data.get("backdrop_path")
    if path:
        tmp = dest + ".mtmp.jpg"
        if await _dl(session, _ORIG + path, tmp):
            if _is_good_image(tmp):
                if title:
                    _overlay_title(tmp, dest, title)
                    try: os.remove(tmp)
                    except Exception: pass
                else:
                    os.rename(tmp, dest)
                return True
            try: os.remove(tmp)
            except Exception: pass
    return False


# ── Source 4: iTunes → portrait to landscape ─────────────────

async def _itunes(session, title, mtype, dest):
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
            tmp = dest + ".itmp.jpg"
            if await _dl(session, hd, tmp) and _is_good_image(tmp):
                ok = _portrait_to_landscape(tmp, dest, title)
                try: os.remove(tmp)
                except Exception: pass
                if ok:
                    return True
    return False


# ── Title overlay on backdrop ─────────────────────────────────

def _overlay_title(src: str, dest: str, title: str) -> bool:
    """
    Overlay the movie title on a backdrop image.
    Creates a gradient bar at the bottom with large white title text.
    This ensures every backdrop has the movie title visible on it.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.open(src).convert("RGB")
        W, H = img.size

        # Resize to 1280×720 if needed
        if W != 1280 or H != 720:
            img = img.resize((1280, 720), Image.LANCZOS)
            W, H = 1280, 720

        canvas = img.convert("RGBA")

        # Gradient bar at bottom (160px tall)
        grad = Image.new("RGBA", (W, 180), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(180):
            alpha = int(230 * (y / 179) ** 1.4)
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad, dest=(0, H - 180))

        draw       = ImageDraw.Draw(canvas)
        title_font = _best_font(_FONTS, 54)
        sub_font   = _best_font(_FONT_REGULAR, 24)

        # Wrap title
        lines = _wrap_text(draw, title.upper(), title_font, W - 100)
        line_h = 62
        total_h = len(lines) * line_h
        text_y  = H - 30 - total_h

        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                lw = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * 28
            lx = (W - lw) // 2
            # Shadow
            draw.text((lx + 2, text_y + 2), line, font=title_font,
                      fill=(0, 0, 0, 200))
            # Main text
            draw.text((lx, text_y), line, font=title_font,
                      fill=(255, 255, 255, 255))
            text_y += line_h

        canvas.convert("RGB").save(dest, "JPEG", quality=95, subsampling=0)
        return True
    except Exception:
        # Just copy if PIL fails
        try:
            import shutil
            shutil.copy2(src, dest)
            return True
        except Exception:
            return False


# ── Portrait → Landscape ──────────────────────────────────────

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
    try:
        from PIL import Image, ImageFilter, ImageDraw
        W, H = 1280, 720
        img = Image.open(input_path).convert("RGB")
        iw, ih = img.size

        # Background: fill → blur → darken
        bg_sc = max(W / iw, H / ih)
        bg    = img.resize((int(iw * bg_sc), int(ih * bg_sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg    = bg.crop(((bw - W) // 2, (bh - H) // 2,
                          (bw - W) // 2 + W, (bh - H) // 2 + H))
        bg    = bg.filter(ImageFilter.GaussianBlur(radius=22))
        dark  = Image.new("RGB", (W, H), (0, 0, 0))
        canvas = Image.blend(bg, dark, alpha=0.55).convert("RGBA")

        # Foreground: sharp poster
        pad_top    = 36
        pad_bottom = 130 if title else 40
        avail_h    = H - pad_top - pad_bottom
        avail_w    = int(W * 0.52)
        fg_sc = min(avail_w / iw, avail_h / ih)
        fw, fh = int(iw * fg_sc), int(ih * fg_sc)
        fg = img.resize((fw, fh), Image.LANCZOS)

        # Drop shadow
        shadow = Image.new("RGBA", (fw + 16, fh + 16), (0, 0, 0, 0))
        sb = Image.new("RGBA", (fw, fh), (0, 0, 0, 140))
        shadow.paste(sb, (8, 8))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10))
        sx = (W - fw) // 2 - 8
        sy = pad_top + (avail_h - fh) // 2 - 8
        canvas.alpha_composite(shadow, dest=(max(0, sx), max(0, sy)))
        canvas.paste(fg, ((W - fw) // 2, pad_top + (avail_h - fh) // 2))

        # Title overlay
        if title:
            grad = Image.new("RGBA", (W, 180), (0, 0, 0, 0))
            gd   = ImageDraw.Draw(grad)
            for y in range(180):
                alpha = int(230 * (y / 179) ** 1.4)
                gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
            canvas.alpha_composite(grad, dest=(0, H - 180))

            draw       = ImageDraw.Draw(canvas)
            title_font = _best_font(_FONTS, 54)
            lines      = _wrap_text(draw, title.upper(), title_font, W - 100)
            line_h     = 62
            text_y     = H - 30 - len(lines) * line_h
            for line in lines:
                try:
                    bbox = draw.textbbox((0, 0), line, font=title_font)
                    lw = bbox[2] - bbox[0]
                except Exception:
                    lw = len(line) * 28
                lx = (W - lw) // 2
                draw.text((lx + 2, text_y + 2), line, font=title_font, fill=(0, 0, 0, 200))
                draw.text((lx, text_y), line, font=title_font, fill=(255, 255, 255, 255))
                text_y += line_h

        canvas.convert("RGB").save(output_path, "JPEG", quality=95, subsampling=0)
        return True
    except Exception:
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


# ── Source 5: Generated title card (guaranteed fallback) ─────

def generate_title_card(title: str, output_path: str,
                        year: str = "", genre: str = "") -> bool:
    """
    Generate a branded 1280×720 title card when no image is available.

    Design:
      - Dark gradient background (deep blue-black, not flat black)
      - Subtle film-grain texture overlay
      - Large white title text (centred)
      - Year / genre in smaller text below
      - NXT HUB watermark bottom-right
      - Decorative lines for polish
    """
    try:
        import random
        from PIL import Image, ImageDraw, ImageFilter

        W, H = 1280, 720
        canvas = Image.new("RGBA", (W, H))
        draw   = ImageDraw.Draw(canvas)

        # ── Background: multi-stop gradient ──────────────────
        # Deep cinematic dark blue to near-black
        top_colour    = (8, 15, 35)
        bottom_colour = (2, 5, 12)
        for y in range(H):
            t = y / H
            r = int(top_colour[0] + (bottom_colour[0] - top_colour[0]) * t)
            g = int(top_colour[1] + (bottom_colour[1] - top_colour[1]) * t)
            b = int(top_colour[2] + (bottom_colour[2] - top_colour[2]) * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

        # ── Film grain texture ────────────────────────────────
        rng = random.Random(hash(title) % (2**31))
        for _ in range(18000):
            x  = rng.randint(0, W - 1)
            y  = rng.randint(0, H - 1)
            br = rng.randint(12, 40)
            draw.point((x, y), fill=(br, br, br, rng.randint(30, 70)))

        # ── Decorative horizontal lines ───────────────────────
        accent = (220, 170, 30)   # golden accent
        for i, y_pos in enumerate([H // 2 - 90, H // 2 + 90]):
            alpha = 180 if i == 0 else 120
            draw.line([(120, y_pos), (W - 120, y_pos)],
                      fill=(*accent, alpha), width=1)

        # ── Corner accent marks ───────────────────────────────
        cl = 40   # corner length
        ct = 2    # corner thickness
        ca = (220, 170, 30, 160)
        for cx, cy, dx, dy in [(80, 80, 1, 1), (W-80, 80, -1, 1),
                                (80, H-80, 1, -1), (W-80, H-80, -1, -1)]:
            draw.line([(cx, cy), (cx + dx * cl, cy)], fill=ca, width=ct)
            draw.line([(cx, cy), (cx, cy + dy * cl)], fill=ca, width=ct)

        # ── NXT HUB logo top-left ─────────────────────────────
        brand_font = _best_font(_FONTS, 20)
        draw.text((80, 58), f"⚡ {getattr(config, 'WATERMARK', 'NXT HUB')}",
                  font=brand_font, fill=(220, 170, 30, 200))

        # ── Title text (centred, wrapped) ─────────────────────
        title_font = _best_font(_FONTS, 72)
        lines      = _wrap_text(draw, title.upper(), title_font, W - 180)
        line_h     = 82
        total_h    = len(lines) * line_h
        text_y     = (H - total_h) // 2 - 20

        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                lw = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * 36
            lx = (W - lw) // 2

            # Glow effect — multiple blurred shadows
            for offset, alpha in [(4, 60), (2, 100), (1, 140)]:
                draw.text((lx + offset, text_y + offset), line,
                          font=title_font, fill=(30, 60, 120, alpha))
            # Main text white
            draw.text((lx, text_y), line, font=title_font,
                      fill=(255, 255, 255, 255))
            text_y += line_h

        # ── Year / genre subtitle ─────────────────────────────
        sub_parts = [p for p in [year, genre] if p]
        if sub_parts:
            sub_text = "  ·  ".join(sub_parts)
            sub_font = _best_font(_FONT_REGULAR, 28)
            try:
                bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
                sw = bbox[2] - bbox[0]
            except Exception:
                sw = len(sub_text) * 14
            draw.text(((W - sw) // 2, text_y + 8), sub_text,
                      font=sub_font, fill=(180, 180, 200, 200))

        # ── Save ──────────────────────────────────────────────
        canvas.convert("RGB").save(output_path, "JPEG", quality=95, subsampling=0)
        return True

    except Exception as e:
        import logging
        logging.getLogger("thumbnail").error(f"generate_title_card failed: {e}")
        return False


# ── Main public function ──────────────────────────────────────

async def get_thumbnail(title: str, year: str | None, dest: str,
                        title_overlay: str = "") -> bool:
    """
    Fetch or generate HD landscape thumbnail with movie title visible.

    1. Tries Fanart (moviethumb has logo baked in — best)
    2. Tries TMDB backdrops + overlays title text
    3. Tries iTunes poster + converts to landscape with title
    4. Falls back to generate_title_card() — always works
    """
    overlay = title_overlay or title
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        tmdb_id, mtype = await _tmdb_search(session, title, year)

        if tmdb_id:
            # Source 1: Fanart moviethumb (has title/logo baked in)
            if await _fanart(session, tmdb_id, mtype, dest, overlay):
                return True

            # Source 2: TMDB /images backdrops + title overlay
            if await _tmdb_images(session, tmdb_id, mtype, dest, overlay):
                return True

            # Source 3: TMDB main backdrop + title overlay
            if await _tmdb_main(session, tmdb_id, mtype, dest, overlay):
                return True

        # Source 4: iTunes portrait → landscape with title
        mt = mtype if tmdb_id else "movie"
        if await _itunes(session, title, mt, dest):
            return True

    # Source 5: Generated title card — always succeeds
    genre = ""
    return generate_title_card(overlay, dest, year or "", genre)
