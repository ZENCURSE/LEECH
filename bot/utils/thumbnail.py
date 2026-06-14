"""
thumbnail.py — HD Movie Background + Title Logo Thumbnail

Exact flow for each upload:
  1. Fanart.tv: moviebackground (1920×1080) + hdmovielogo (transparent PNG)
               → composite: backdrop with real movie title logo overlaid
  2. Fanart.tv: moviethumb (1000×562) — already has logo baked in by designers
  3. TMDB: best backdrop + TMDB logo PNG → composite
  4. iTunes: portrait poster → landscape conversion with title text
  5. generate_title_card() — guaranteed fallback, always produces result

Priority within each Fanart type: sorted by likes desc (most popular first).
"""

import os
import re
import asyncio
import aiohttp
import config

_TMDB         = "https://api.themoviedb.org/3"
_ORIG         = "https://image.tmdb.org/t/p/original"
_W500         = "https://image.tmdb.org/t/p/w500"
_FANART_MOVIE = "https://webservice.fanart.tv/v3/movies"
_FANART_TV    = "https://webservice.fanart.tv/v3/tv"
_HEADERS      = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "image/webp,image/jpeg,image/png,*/*",
    "Accept-Encoding": "gzip, deflate",
}
_TIMEOUT    = aiohttp.ClientTimeout(total=60, connect=10)
_MIN_BYTES  = 10_000
_SEARCH_LANGS = ["en-US", "hi-IN", "te-IN", "ta-IN", "ml-IN"]

_FONTS = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
_FONTS_REG = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _best_font(paths, size):
    from PIL import ImageFont
    for p in paths:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


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


async def _dl_bytes(session, url) -> bytes | None:
    """Download raw bytes — used for logo PNGs that need transparency."""
    try:
        async with session.get(url, headers=_HEADERS, timeout=_TIMEOUT,
                               allow_redirects=True) as r:
            if r.status != 200:
                return None
            data = await r.read()
            return data if len(data) >= 5_000 else None
    except Exception:
        return None


async def _dl(session, url, dest) -> bool:
    """Download image → JPEG q=95 subsampling=0."""
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


# ── Quality check ─────────────────────────────────────────────

def _is_good_image(path: str) -> bool:
    """Reject only truly broken images: solid colour or completely black."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(path).convert("RGB").resize((160, 90), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32)
        if arr.mean() < 5:   return False   # completely black
        if arr.mean() > 245: return False   # completely white
        if arr.std()  < 8:   return False   # solid colour = broken
        return True
    except Exception:
        return True


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


# ── Logo compositor ───────────────────────────────────────────

def _composite_logo(backdrop_path: str, logo_data: bytes, output_path: str,
                    target_w=1280, target_h=720) -> bool:
    """
    Composite a transparent PNG logo onto a backdrop image.

    Layout:
      - Backdrop scaled to 1280×720 (crop to fill, not letterbox)
      - Subtle dark gradient at bottom-left for logo readability
      - Logo scaled to 45% of backdrop width, max height 200px
      - Positioned bottom-left with 60px padding
      - Drop shadow under logo for depth
    """
    try:
        from PIL import Image, ImageFilter, ImageDraw
        import io

        W, H = target_w, target_h

        # ── Backdrop ──────────────────────────────────────────
        bg = Image.open(backdrop_path).convert("RGB")
        bw, bh = bg.size
        scale  = max(W / bw, H / bh)
        bg     = bg.resize((int(bw * scale), int(bh * scale)), Image.LANCZOS)
        bw, bh = bg.size
        bg     = bg.crop(((bw - W) // 2, (bh - H) // 2,
                           (bw - W) // 2 + W, (bh - H) // 2 + H))
        canvas = bg.convert("RGBA")

        # ── Gradient vignette at bottom for logo readability ──
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(H):
            # Gradient strongest at bottom, fades toward center
            t = max(0, (y - H * 0.45) / (H * 0.55))
            alpha = int(185 * (t ** 1.6))
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad)

        # ── Logo ──────────────────────────────────────────────
        logo = Image.open(io.BytesIO(logo_data)).convert("RGBA")
        lw, lh = logo.size

        # Scale: max 45% of backdrop width, max 200px tall
        max_lw = int(W * 0.45)
        max_lh = 200
        scale  = min(max_lw / lw, max_lh / lh, 1.0)
        lw, lh = int(lw * scale), int(lh * scale)
        logo   = logo.resize((lw, lh), Image.LANCZOS)

        # Position: bottom-left with 60px padding
        pad  = 60
        lx   = pad
        ly   = H - lh - pad

        # Drop shadow
        shadow = Image.new("RGBA", (lw + 20, lh + 20), (0, 0, 0, 0))
        # Extract alpha from logo, darken it for shadow
        r, g, b, a = logo.split()
        shadow_a = a.point(lambda p: int(p * 0.6))
        shadow_rgb = Image.new("RGB", (lw, lh), (0, 0, 0))
        sr, sg, sb = shadow_rgb.split()
        shadow_img = Image.merge("RGBA", (sr, sg, sb, shadow_a))
        shadow.paste(shadow_img, (10, 10))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=8))
        canvas.alpha_composite(shadow, dest=(lx - 4, ly - 4))

        # Paste logo
        canvas.alpha_composite(logo, dest=(lx, ly))

        canvas.convert("RGB").save(output_path, "JPEG", quality=95, subsampling=0)
        return True

    except Exception as e:
        import logging
        logging.getLogger("thumbnail").error(f"_composite_logo failed: {e}")
        return False


# ── Source 1: Fanart backdrop + logo composite ────────────────

async def _fanart_composite(session, tmdb_id, mtype, dest, title="") -> bool:
    """
    Fetch moviebackground + hdmovielogo from Fanart.tv and composite them.
    This gives: real movie backdrop with the official title logo placed on it.
    """
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

    # Get logo types (sorted by likes)
    logo_key  = "hdmovielogo"  if mtype == "movie" else "hdtvlogo"
    bg_key    = "moviebackground" if mtype == "movie" else "showbackground"
    thumb_key = "moviethumb"   if mtype == "movie" else "tvthumb"

    logos = sorted(data.get(logo_key, []),
                   key=lambda x: int(x.get("likes", 0)), reverse=True)
    bgs   = sorted(data.get(bg_key, []),
                   key=lambda x: int(x.get("likes", 0)), reverse=True)

    # Try: backdrop + logo composite (best option)
    if logos and bgs:
        for bg_art in bgs[:5]:
            bg_url = bg_art.get("url", "")
            if not bg_url:
                continue
            bg_tmp = dest + ".bg.jpg"
            if not await _dl(session, bg_url, bg_tmp):
                continue
            if not _is_good_image(bg_tmp):
                try: os.remove(bg_tmp)
                except Exception: pass
                continue

            # Try each logo
            for logo_art in logos[:5]:
                logo_url = logo_art.get("url", "")
                if not logo_url:
                    continue
                logo_bytes = await _dl_bytes(session, logo_url)
                if not logo_bytes:
                    continue
                if _composite_logo(bg_tmp, logo_bytes, dest):
                    try: os.remove(bg_tmp)
                    except Exception: pass
                    return True

            # No logo worked — use backdrop with text overlay
            if _is_good_image(bg_tmp):
                _overlay_title(bg_tmp, dest, title)
                try: os.remove(bg_tmp)
                except Exception: pass
                return True

    # Fallback: moviethumb (logo already baked in by Fanart designers)
    thumbs = sorted(data.get(thumb_key, []),
                    key=lambda x: int(x.get("likes", 0)), reverse=True)
    for art in thumbs[:5]:
        url = art.get("url", "")
        if url and await _dl(session, url, dest) and _is_good_image(dest):
            return True

    # Fallback: just backdrop with text overlay
    for bg_art in bgs[:5]:
        bg_url = bg_art.get("url", "")
        if not bg_url:
            continue
        tmp = dest + ".bgtmp.jpg"
        if await _dl(session, bg_url, tmp) and _is_good_image(tmp):
            _overlay_title(tmp, dest, title)
            try: os.remove(tmp)
            except Exception: pass
            return True

    return False


# ── Source 2: TMDB backdrop + logo composite ─────────────────

async def _tmdb_composite(session, tmdb_id, mtype, dest, title="") -> bool:
    """
    Fetch TMDB backdrop + logo PNG and composite them.
    TMDB has official title logos under /images with type filtering.
    """
    if not getattr(config, "TMDB_API_KEY", ""):
        return False

    data = await _get(
        session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
        {"api_key": config.TMDB_API_KEY,
         "include_image_language": "en,hi,te,ta,null"},
    )

    backdrops = sorted(
        data.get("backdrops", []),
        key=lambda x: (float(x.get("vote_average", 0)), int(x.get("vote_count", 0))),
        reverse=True,
    )
    logos = sorted(
        data.get("logos", []),
        key=lambda x: (float(x.get("vote_average", 0)), int(x.get("vote_count", 0))),
        reverse=True,
    )

    # Try backdrop + logo composite
    for bd in backdrops[:5]:
        bg_path = bd.get("file_path", "")
        if not bg_path:
            continue
        bg_tmp = dest + ".tmbg.jpg"
        if not await _dl(session, _ORIG + bg_path, bg_tmp):
            continue
        if not _is_good_image(bg_tmp):
            try: os.remove(bg_tmp)
            except Exception: pass
            continue

        # Try logos (prefer PNG for transparency)
        png_logos = [l for l in logos if l.get("file_path", "").endswith(".png")]
        for logo in (png_logos or logos)[:5]:
            logo_path = logo.get("file_path", "")
            if not logo_path:
                continue
            logo_bytes = await _dl_bytes(session, _ORIG + logo_path)
            if logo_bytes and _composite_logo(bg_tmp, logo_bytes, dest):
                try: os.remove(bg_tmp)
                except Exception: pass
                return True

        # No logo — overlay title text
        _overlay_title(bg_tmp, dest, title)
        try: os.remove(bg_tmp)
        except Exception: pass
        return True

    return False


# ── Source 3: iTunes portrait → landscape ────────────────────

async def _itunes(session, title, mtype, dest) -> bool:
    entity = "movie" if mtype == "movie" else "tvShow"
    for country in ("in", "us"):
        data = await _get(
            session, "https://itunes.apple.com/search",
            {"term": title, "media": "movie" if mtype == "movie" else "tvShow",
             "entity": entity, "limit": "6", "country": country},
        )
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


# ── Title overlay (backdrop → backdrop + text) ────────────────

def _overlay_title(src: str, dest: str, title: str) -> bool:
    """Overlay movie title text on a backdrop when no logo PNG is available."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
        img = Image.open(src).convert("RGB")
        W, H = img.size
        if W != 1280 or H != 720:
            # Scale to fill 1280×720
            scale = max(1280 / W, 720 / H)
            img   = img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
            iw, ih = img.size
            img   = img.crop(((iw - 1280) // 2, (ih - 720) // 2,
                               (iw - 1280) // 2 + 1280, (ih - 720) // 2 + 720))
            W, H  = 1280, 720

        canvas = img.convert("RGBA")
        grad   = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
        gd     = ImageDraw.Draw(grad)
        for y in range(200):
            alpha = int(220 * (y / 199) ** 1.5)
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad, dest=(0, H - 200))

        draw  = ImageDraw.Draw(canvas)
        font  = _best_font(_FONTS, 58)
        lines = _wrap_text(draw, title.upper(), font, W - 120)
        lh    = 68
        ty    = H - 36 - len(lines) * lh
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                lw   = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * 28
            lx = (W - lw) // 2
            draw.text((lx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 200))
            draw.text((lx, ty), line, font=font, fill=(255, 255, 255, 255))
            ty += lh

        canvas.convert("RGB").save(dest, "JPEG", quality=95, subsampling=0)
        return True
    except Exception:
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
        img  = Image.open(input_path).convert("RGB")
        iw, ih = img.size

        bg_sc = max(W / iw, H / ih)
        bg    = img.resize((int(iw * bg_sc), int(ih * bg_sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg    = bg.crop(((bw - W) // 2, (bh - H) // 2,
                          (bw - W) // 2 + W, (bh - H) // 2 + H))
        bg    = bg.filter(ImageFilter.GaussianBlur(radius=22))
        dark  = Image.new("RGB", (W, H), (0, 0, 0))
        canvas = Image.blend(bg, dark, alpha=0.55).convert("RGBA")

        pad_top    = 36
        pad_bottom = 140 if title else 40
        avail_h    = H - pad_top - pad_bottom
        avail_w    = int(W * 0.52)
        fg_sc = min(avail_w / iw, avail_h / ih)
        fw, fh = int(iw * fg_sc), int(ih * fg_sc)
        fg = img.resize((fw, fh), Image.LANCZOS)

        shadow = Image.new("RGBA", (fw + 16, fh + 16), (0, 0, 0, 0))
        sb = Image.new("RGBA", (fw, fh), (0, 0, 0, 140))
        shadow.paste(sb, (8, 8))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10))
        canvas.alpha_composite(shadow, dest=(max(0, (W - fw) // 2 - 8),
                                             max(0, pad_top + (avail_h - fh) // 2 - 8)))
        canvas.paste(fg, ((W - fw) // 2, pad_top + (avail_h - fh) // 2))

        if title:
            grad = Image.new("RGBA", (W, 200), (0, 0, 0, 0))
            gd   = ImageDraw.Draw(grad)
            for y in range(200):
                alpha = int(230 * (y / 199) ** 1.4)
                gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
            canvas.alpha_composite(grad, dest=(0, H - 200))

            draw  = ImageDraw.Draw(canvas)
            font  = _best_font(_FONTS, 58)
            lines = _wrap_text(draw, title.upper(), font, W - 120)
            lh    = 68
            ty    = H - 36 - len(lines) * lh
            for line in lines:
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    lw   = bbox[2] - bbox[0]
                except Exception:
                    lw = len(line) * 28
                lx = (W - lw) // 2
                draw.text((lx + 2, ty + 2), line, font=font, fill=(0, 0, 0, 200))
                draw.text((lx, ty), line, font=font, fill=(255, 255, 255, 255))
                ty += lh

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


# ── Source 5: Generated title card ───────────────────────────

def generate_title_card(title: str, output_path: str,
                        year: str = "", genre: str = "") -> bool:
    """Guaranteed fallback — cinematic dark gradient with large title text."""
    try:
        import random
        from PIL import Image, ImageDraw
        W, H = 1280, 720
        canvas = Image.new("RGBA", (W, H))
        draw   = ImageDraw.Draw(canvas)

        # Multi-stop gradient background
        top_c, bot_c = (8, 15, 35), (2, 5, 12)
        for y in range(H):
            t = y / H
            r = int(top_c[0] + (bot_c[0] - top_c[0]) * t)
            g = int(top_c[1] + (bot_c[1] - top_c[1]) * t)
            b = int(top_c[2] + (bot_c[2] - top_c[2]) * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

        # Film grain
        rng = random.Random(hash(title) % (2**31))
        for _ in range(18000):
            x, y = rng.randint(0, W-1), rng.randint(0, H-1)
            br   = rng.randint(12, 40)
            draw.point((x, y), fill=(br, br, br, rng.randint(30, 70)))

        # Accent lines
        acc = (220, 170, 30)
        for y_pos in [H // 2 - 90, H // 2 + 90]:
            draw.line([(120, y_pos), (W-120, y_pos)], fill=(*acc, 160), width=1)

        # Corner marks
        for cx, cy, dx, dy in [(80,80,1,1),(W-80,80,-1,1),(80,H-80,1,-1),(W-80,H-80,-1,-1)]:
            draw.line([(cx, cy), (cx+dx*40, cy)], fill=(*acc, 160), width=2)
            draw.line([(cx, cy), (cx, cy+dy*40)], fill=(*acc, 160), width=2)

        # Watermark
        wm_font = _best_font(_FONTS, 20)
        draw.text((80, 58), f"⚡ {getattr(config,'WATERMARK','NXT HUB')}",
                  font=wm_font, fill=(220, 170, 30, 200))

        # Title
        title_font = _best_font(_FONTS, 72)
        lines = _wrap_text(draw, title.upper(), title_font, W - 180)
        lh    = 84
        ty    = (H - len(lines) * lh) // 2 - 20
        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                lw   = bbox[2] - bbox[0]
            except Exception:
                lw = len(line) * 36
            lx = (W - lw) // 2
            for off, a in [(4, 60), (2, 100), (1, 140)]:
                draw.text((lx+off, ty+off), line, font=title_font, fill=(30,60,120,a))
            draw.text((lx, ty), line, font=title_font, fill=(255, 255, 255, 255))
            ty += lh

        # Year / genre
        sub_parts = [p for p in [year, genre] if p]
        if sub_parts:
            sf = _best_font(_FONTS_REG, 28)
            sub = "  ·  ".join(sub_parts)
            try:
                bbox = draw.textbbox((0, 0), sub, font=sf)
                sw   = bbox[2] - bbox[0]
            except Exception:
                sw = len(sub) * 14
            draw.text(((W-sw)//2, ty+8), sub, font=sf, fill=(180,180,200,200))

        canvas.convert("RGB").save(output_path, "JPEG", quality=95, subsampling=0)
        return True
    except Exception:
        return False


# ── has_text_region ───────────────────────────────────────────

def _has_text_region(path: str) -> bool:
    try:
        import numpy as np
        from PIL import Image, ImageFilter
        img    = Image.open(path).convert("L").resize((320, 180), Image.LANCZOS)
        W, H   = img.size
        bottom = img.crop((0, int(H*0.70), W, H))
        edge   = bottom.filter(ImageFilter.FIND_EDGES)
        arr    = np.array(edge, dtype=np.float32)
        return int((arr > 30).sum()) > 3000
    except Exception:
        return True


# ── Main ─────────────────────────────────────────────────────

async def get_thumbnail(title: str, year: str | None, dest: str,
                        title_overlay: str = "") -> bool:
    """
    Fetch or generate HD movie background with title logo.
    Always succeeds — falls back to generate_title_card() if all sources fail.
    """
    overlay = title_overlay or title
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        tmdb_id, mtype = await _tmdb_search(session, title, year)

        if tmdb_id:
            # Best: Fanart backdrop + hdmovielogo composite
            if await _fanart_composite(session, tmdb_id, mtype, dest, overlay):
                return True
            # Good: TMDB backdrop + TMDB logo composite
            if await _tmdb_composite(session, tmdb_id, mtype, dest, overlay):
                return True

        # iTunes portrait → landscape with title text
        mt = mtype if tmdb_id else "movie"
        if await _itunes(session, title, mt, dest):
            return True

    # Guaranteed: branded title card
    return generate_title_card(overlay, dest, year or "", "")
