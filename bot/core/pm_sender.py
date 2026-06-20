"""
pm_sender.py — aiogram PM sender with HD cover support — NXTL
==============================================================
Sends files to user's PM via aiogram>=3.18.0 bot which properly supports
the cover= parameter as a full photo upload (not just a thumbnail).

Why aiogram 3.18.0?
  - aiogram 3.18.0 added proper cover= support in send_video()
  - The cover is uploaded as a full InputMediaPhoto, not an InputFile thumb
  - Telegram stores it at full resolution → beautiful HD poster in video player
  - pyrofork's cover= also works, but aiogram 3.18+ is more reliable for PM bots

Flow:
  1. fetch_movie_poster(title, year) → gets the REAL movie poster (1280×720 HQ JPEG)
  2. send_file_to_pm(bot, user_id, file_path, title, year) → sends with HD cover

Integration:
  Import send_file_to_pm and call it instead of directly messaging the user.
  The same high-quality poster used as cover= is shown when the video is played.

Requirements:
  aiogram==3.18.0  (in requirements.txt)
  TMDB_API_KEY, FANART_API_KEY, OMDB_API_KEY in config.py (optional but recommended)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Optional

import aiohttp
import aiofiles

import config
from bot import LOGGER

# ── Constants ──────────────────────────────────────────────────
_TMDB      = "https://api.themoviedb.org/3"
_ORIG      = "https://image.tmdb.org/t/p/original"
_W1280     = "https://image.tmdb.org/t/p/w1280"
_W780      = "https://image.tmdb.org/t/p/w780"
_FANART    = "https://webservice.fanart.tv/v3/movies"
_OMDB      = "https://www.omdbapi.com/"
_ITUNES    = "https://itunes.apple.com/search"
_UA        = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT   = aiohttp.ClientTimeout(total=30, connect=10)
_W, _H     = 1280, 720
_COVER_MAX = 5 * 1024 * 1024   # 5 MB — max for cover= full photo
_MIN_BYTES = 8_000


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOW-LEVEL HTTP HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _json(session: aiohttp.ClientSession, url: str, params: dict = None) -> dict:
    try:
        async with session.get(
            url, params=params,
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        ) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception as e:
        LOGGER.debug(f"[PmSender] GET JSON {url}: {e}")
    return {}


async def _raw(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(
            url, headers={"User-Agent": _UA}, timeout=_TIMEOUT,
        ) as r:
            if r.status == 200:
                data = await r.read()
                return data if len(data) >= _MIN_BYTES else None
    except Exception as e:
        LOGGER.debug(f"[PmSender] GET bytes {url}: {e}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  IMAGE PROCESSING HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _poster_to_landscape(data: bytes, title: str = "") -> bytes | None:
    """
    Convert a portrait movie poster to 1280x720 cinematic landscape JPEG.

    The POSTER IS THE HERO - placed CENTER STAGE at full canvas height.
    The actual movie title artwork baked into the poster is fully visible.

    Layout:
      - Poster fills FULL HEIGHT of canvas, centered horizontally
      - Both sides: blurred, darkened, desaturated version of the same poster
        (seamlessly extends the poster colour palette as cinematic wings)
      - Soft vignette edges blend the sides into the center poster
      - NO extra text -- the poster own title art IS the title
      - Soft drop shadow around the poster for depth
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

        img    = Image.open(io.BytesIO(data)).convert("RGB")
        iw, ih = img.size

        # Scale poster to fill FULL canvas height
        scale   = _H / ih
        fw, fh  = int(iw * scale), int(ih * scale)
        if fw > _W:   # unusually wide poster -- fit by width instead
            scale = _W / iw
            fw, fh = int(iw * scale), int(ih * scale)

        poster_main = img.resize((fw, fh), Image.LANCZOS)

        # Background: same poster stretched wide, heavily blurred + darkened
        bg_scale = max(_W / iw, _H / ih) * 1.05
        bg = img.resize((int(iw * bg_scale), int(ih * bg_scale)), Image.LANCZOS)
        bw, bh = bg.size
        bx = (bw - _W) // 2
        by = (bh - _H) // 2
        bg = bg.crop((bx, by, bx + _W, by + _H))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=32))
        bg = ImageEnhance.Brightness(bg).enhance(0.28)
        # Slight desaturate for cinematic look
        bg_grey = bg.convert("L").convert("RGB")
        bg = Image.blend(bg, bg_grey, alpha=0.4)

        canvas = bg.convert("RGBA")

        # Center the poster
        px = (_W - fw) // 2
        py = (_H - fh) // 2

        # Drop shadow behind poster
        shadow_pad = 24
        shadow = Image.new("RGBA", (fw + shadow_pad * 2, fh + shadow_pad * 2), (0, 0, 0, 0))
        sb     = Image.new("RGBA", (fw, fh), (0, 0, 0, 180))
        shadow.paste(sb, (shadow_pad, shadow_pad))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=22))
        canvas.alpha_composite(shadow, dest=(max(0, px - shadow_pad), max(0, py - shadow_pad)))

        # Paste the real poster -- CENTERED, FULL HEIGHT
        canvas.alpha_composite(poster_main.convert("RGBA"), dest=(px, py))

        # Vignette: soft dark edges left & right
        vign_w = max(px + 40, 80)
        for side_x, flip in ((0, False), (_W, True)):
            vign = Image.new("RGBA", (vign_w, _H), (0, 0, 0, 0))
            gd   = ImageDraw.Draw(vign)
            for x in range(vign_w):
                t     = 1.0 - (x / vign_w) ** 0.6
                alpha = int(200 * t)
                gd.line([(x, 0), (x, _H)], fill=(0, 0, 0, alpha))
            if flip:
                vign = vign.transpose(Image.FLIP_LEFT_RIGHT)
                canvas.alpha_composite(vign, dest=(_W - vign_w, 0))
            else:
                canvas.alpha_composite(vign, dest=(0, 0))

        # Export at FULL quality
        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0, optimize=True)
        result = buf.getvalue()
        return result if len(result) <= _COVER_MAX else None

    except Exception as e:
        LOGGER.debug(f"[PmSender] _poster_to_landscape: {e}")
        return None


def _composite_logo(bg_data: bytes, logo_data: bytes, title: str = "") -> bytes | None:
    """Composite a transparent logo PNG onto a backdrop image."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

        W, H = _W, _H
        bg     = Image.open(io.BytesIO(bg_data)).convert("RGB")

        # Scale/crop bg to 1280×720
        iw, ih = bg.size
        sc  = max(W / iw, H / ih)
        bg  = bg.resize((int(iw * sc), int(ih * sc)), Image.LANCZOS)
        bw, bh = bg.size
        bg  = bg.crop(((bw - W) // 2, (bh - H) // 2,
                        (bw - W) // 2 + W, (bh - H) // 2 + H))
        canvas = bg.convert("RGBA")

        # Cinematic gradient overlay
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd   = ImageDraw.Draw(grad)
        for y in range(H):
            t     = max(0.0, (y - H * 0.35) / (H * 0.65))
            alpha = int(210 * (t ** 1.3))
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad)

        # Logo
        logo   = Image.open(io.BytesIO(logo_data)).convert("RGBA")
        lw, lh = logo.size
        max_lw, max_lh = 520, 200
        sc2 = min(max_lw / lw, max_lh / lh, 1.0)
        lw  = max(int(lw * sc2), 1)
        lh  = max(int(lh * sc2), 1)
        logo = logo.resize((lw, lh), Image.LANCZOS)

        # Brighten logo so it's visible on dark bg
        r, g, b, a = logo.split()
        rgb = Image.merge("RGB", (r, g, b))
        rgb = ImageEnhance.Brightness(rgb).enhance(1.25)
        r, g, b = rgb.split()
        logo = Image.merge("RGBA", (r, g, b, a))

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

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0, optimize=True)
        return buf.getvalue()
    except Exception as e:
        LOGGER.debug(f"[PmSender] _composite_logo: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  POSTER FETCHER — returns high-quality JPEG bytes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def fetch_movie_poster(title: str, year: str | int | None = None) -> bytes | None:
    """
    Fetch the ACTUAL movie poster for a title.

    Returns raw JPEG bytes of a 1280×720 landscape image featuring the
    real movie artwork (logo + backdrop, or portrait poster adapted to landscape).

    Priority chain:
      1. TMDB: backdrop + logo PNG composite   (real movie logo text!)
      2. TMDB: actual movie poster → landscape (real poster artwork)
      3. OMDB: IMDb HD poster → landscape
      4. Fanart.tv: hdmovielogo + moviebackground
      5. iTunes: portrait artwork → landscape
      Returns None if all fail (caller should fall back to title card)
    """
    LOGGER.info(f"[PmSender] Fetching poster: '{title}' ({year})")

    tmdb_key   = getattr(config, "TMDB_API_KEY",   "").strip()
    fanart_key = getattr(config, "FANART_API_KEY",  "").strip()
    omdb_key   = getattr(config, "OMDB_API_KEY",    "").strip()

    async with aiohttp.ClientSession() as s:

        # ── 1 & 2: TMDB ──────────────────────────────────────
        if tmdb_key:
            tmdb_id, mtype = await _tmdb_search(s, tmdb_key, title, year)
            if tmdb_id:
                result = await _tmdb_poster(s, tmdb_key, tmdb_id, mtype, title)
                if result:
                    LOGGER.info("[PmSender] ✅ TMDB poster fetched")
                    return result

                # ── Fanart.tv as secondary (needs TMDB ID) ────
                if fanart_key:
                    result = await _fanart_poster(s, tmdb_key, fanart_key, tmdb_id, mtype, title)
                    if result:
                        LOGGER.info("[PmSender] ✅ Fanart poster fetched")
                        return result

        # ── 3: OMDB (IMDb poster) ─────────────────────────────
        if omdb_key:
            result = await _omdb_poster(s, omdb_key, title, year)
            if result:
                LOGGER.info("[PmSender] ✅ OMDB poster fetched")
                return result

        # ── 4: iTunes ─────────────────────────────────────────
        result = await _itunes_poster(s, title)
        if result:
            LOGGER.info("[PmSender] ✅ iTunes poster fetched")
            return result

    LOGGER.warning(f"[PmSender] No poster found for: '{title}' ({year})")
    return None


async def _tmdb_search(session, key: str, title: str, year) -> tuple[int | None, str]:
    for mtype in ("movie", "tv"):
        params = {"api_key": key, "query": title, "include_adult": "false"}
        if year:
            params["year" if mtype == "movie" else "first_air_date_year"] = year
        data = await _json(session, f"{_TMDB}/search/{mtype}", params)
        results = [r for r in data.get("results", []) if r.get("id")]
        if results:
            return results[0]["id"], mtype
    return None, "movie"


async def _tmdb_poster(session, key: str, tmdb_id: int, mtype: str, title: str) -> bytes | None:
    data = await _json(session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
                       {"api_key": key, "include_image_language": "en,hi,te,ta,null"})

    def _sort(lst):
        return sorted(lst, key=lambda x: (float(x.get("vote_average", 0)),
                                          int(x.get("vote_count", 0))), reverse=True)

    backdrops = _sort(data.get("backdrops", []))
    logos     = _sort([l for l in data.get("logos", []) if l.get("file_path", "").endswith(".png")])
    posters   = _sort(data.get("posters", []))

    # Strategy 1: Backdrop + Logo PNG composite (best — real movie logo!)
    if logos and backdrops:
        for bd in backdrops[:5]:
            bg_data = await _raw(session, _W1280 + bd.get("file_path", ""))
            if not bg_data:
                continue
            for logo in logos[:4]:
                logo_data = await _raw(session, _ORIG + logo.get("file_path", ""))
                if not logo_data:
                    continue
                result = _composite_logo(bg_data, logo_data, title)
                if result:
                    return result

    # Strategy 2: Actual movie poster → cinematic landscape (real poster art)
    for poster in posters[:4]:
        fp = poster.get("file_path", "")
        if not fp:
            continue
        # Try original resolution first, then w780 fallback
        data_bytes = await _raw(session, _ORIG + fp) or await _raw(session, _W780 + fp)
        if not data_bytes:
            continue
        result = _poster_to_landscape(data_bytes, title)
        if result:
            return result

    # Strategy 3: Backdrop with no logo (text overlay done by generate_hd_thumb)
    for bd in backdrops[:3]:
        fp = bd.get("file_path", "")
        if not fp:
            continue
        bg_data = await _raw(session, _W1280 + fp)
        if bg_data and len(bg_data) >= _MIN_BYTES:
            # Return the backdrop as-is; generate_hd_thumb will add text overlay
            return bg_data

    return None


async def _fanart_poster(session, tmdb_key: str, fanart_key: str,
                         tmdb_id: int, mtype: str, title: str) -> bytes | None:
    ext = await _json(session, f"{_TMDB}/{mtype}/{tmdb_id}/external_ids",
                      {"api_key": tmdb_key})
    fid = ext.get("imdb_id" if mtype == "movie" else "tvdb_id", "")
    if not fid:
        return None

    data = await _json(session, f"{_FANART}/{fid}", {"api_key": fanart_key})
    if not data:
        return None

    def _top(key, n=4):
        return sorted(data.get(key, []),
                      key=lambda x: int(x.get("likes", 0)), reverse=True)[:n]

    logos = _top("hdmovielogo") + _top("movielogo")
    bgs   = _top("moviebackground") + _top("moviethumb")

    for bg_art in bgs[:4]:
        bg_data = await _raw(session, bg_art.get("url", ""))
        if not bg_data:
            continue
        for logo_art in logos[:4]:
            logo_data = await _raw(session, logo_art.get("url", ""))
            if not logo_data:
                continue
            result = _composite_logo(bg_data, logo_data, title)
            if result:
                return result

    return None


async def _omdb_poster(session, key: str, title: str, year) -> bytes | None:
    import re
    params = {"apikey": key, "t": title, "type": "movie"}
    if year:
        params["y"] = year
    data = await _json(session, _OMDB, params)
    url  = data.get("Poster", "")
    if not url or url == "N/A":
        params["type"] = "series"
        data = await _json(session, _OMDB, params)
        url  = data.get("Poster", "")
    if not url or url == "N/A":
        return None

    # Upgrade to HD: SX300 → SX1000
    hd_url = re.sub(r"_SX\d+", "_SX1000", url)
    hd_url = re.sub(r"_SY\d+", "_SY1000", hd_url)

    data_bytes = await _raw(session, hd_url) or await _raw(session, url)
    if not data_bytes:
        return None
    return _poster_to_landscape(data_bytes, title)


async def _itunes_poster(session, title: str) -> bytes | None:
    import re
    for country in ("us", "in", "gb"):
        data = await _json(session, _ITUNES, {
            "term": title, "media": "movie", "entity": "movie",
            "limit": "8", "country": country,
        })
        for item in data.get("results", [])[:8]:
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if not art:
                continue
            hd  = re.sub(r"/\d+x\d+bb/", "/3000x3000bb/", art)
            raw = await _raw(session, hd)
            if raw and len(raw) >= _MIN_BYTES:
                result = _poster_to_landscape(raw, title)
                if result:
                    return result
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AIOGRAM PM SENDER — send file to user PM with HD cover
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def send_file_to_pm(
    bot,           # aiogram Bot instance
    user_id: int,
    file_path: str,
    title: str = "",
    year: str | int | None = None,
    caption: str = "",
    duration: int = 0,
    width: int = 1280,
    height: int = 720,
) -> bool:
    """
    Send a video/file to a user's PM with a HIGH-QUALITY movie poster as cover.

    Uses aiogram>=3.18.0 send_video(cover=...) which properly uploads the
    poster as a full Telegram Photo — shown at full resolution when video plays.

    Args:
        bot:       aiogram.Bot instance
        user_id:   Telegram user ID to send to
        file_path: Local path to the video file
        title:     Movie/show title (for poster lookup)
        year:      Release year (optional, improves accuracy)
        caption:   Message caption (HTML)
        duration:  Video duration in seconds
        width:     Video width in pixels
        height:    Video height in pixels

    Returns:
        True if sent successfully, False otherwise.
    """
    try:
        from aiogram import Bot
        from aiogram.types import FSInputFile, BufferedInputFile
    except ImportError:
        LOGGER.error("[PmSender] aiogram not installed! Add aiogram==3.18.0 to requirements.txt")
        return False

    if not os.path.isfile(file_path):
        LOGGER.error(f"[PmSender] File not found: {file_path}")
        return False

    # Fetch the actual movie poster
    cover_bytes: bytes | None = None
    if title:
        cover_bytes = await fetch_movie_poster(title, year)

    # Fall back to thumbnail from the unified engine if poster fetch failed
    if not cover_bytes:
        from bot.utils.thumbnail import get_thumbnail
        from bot.utils.thumb_store import TMP_DIR
        tmp_cover = os.path.join(TMP_DIR, f"pm_cover_{user_id}_{int(time.time())}.jpg")
        try:
            await get_thumbnail(title or os.path.basename(file_path), year, tmp_cover,
                                title_overlay=title)
            if os.path.exists(tmp_cover):
                async with aiofiles.open(tmp_cover, "rb") as f:
                    cover_bytes = await f.read()
        except Exception as e:
            LOGGER.warning(f"[PmSender] Fallback thumbnail failed: {e}")
        finally:
            try:
                if os.path.exists(tmp_cover):
                    os.remove(tmp_cover)
            except Exception:
                pass

    # Build cover InputFile — use BufferedInputFile so aiogram uploads it as a
    # proper photo (full resolution), not as a compressed thumbnail
    cover_input = None
    if cover_bytes:
        cover_input = BufferedInputFile(
            file=cover_bytes,
            filename=f"cover_{title or 'poster'}.jpg",
        )

    video_input = FSInputFile(file_path)

    try:
        # aiogram 3.18.0: cover= is passed as a separate photo upload
        # Telegram stores it at full resolution in the video player
        await bot.send_video(
            chat_id=user_id,
            video=video_input,
            caption=caption or f"<b>{title or os.path.basename(file_path)}</b>",
            parse_mode="HTML",
            duration=duration or 0,
            width=width,
            height=height,
            supports_streaming=True,
            cover=cover_input,          # ← HD poster as full photo (aiogram 3.18+)
            disable_notification=False,
        )
        LOGGER.info(f"[PmSender] ✅ Sent to PM uid={user_id} with HD cover")
        return True

    except TypeError:
        # aiogram version < 3.18.0 — cover= not supported, try without it
        LOGGER.warning("[PmSender] cover= not supported in this aiogram version — upgrade to 3.18.0")
        try:
            # Build a small thumbnail (320×320) as fallback thumb=
            from bot.utils.thumb_store import prep_for_upload
            from bot.utils.thumb_store import TMP_DIR
            if cover_bytes:
                tmp_small = os.path.join(TMP_DIR, f"pm_thumb_{user_id}_{int(time.time())}.jpg")
                async with aiofiles.open(tmp_small, "wb") as f:
                    await f.write(cover_bytes)
                try:
                    small_path = prep_for_upload(tmp_small)
                    if small_path:
                        async with aiofiles.open(small_path, "rb") as f:
                            thumb_bytes = await f.read()
                        thumb_input = BufferedInputFile(file=thumb_bytes, filename="thumb.jpg")
                    else:
                        thumb_input = None
                finally:
                    try:
                        os.remove(tmp_small)
                        if small_path and os.path.exists(small_path):
                            os.remove(small_path)
                    except Exception:
                        pass
            else:
                thumb_input = None

            await bot.send_video(
                chat_id=user_id,
                video=video_input,
                caption=caption or f"<b>{title or os.path.basename(file_path)}</b>",
                parse_mode="HTML",
                duration=duration or 0,
                width=width,
                height=height,
                supports_streaming=True,
                thumbnail=thumb_input,  # small preview only
                disable_notification=False,
            )
            LOGGER.info(f"[PmSender] Sent to PM uid={user_id} (no cover= — upgrade aiogram!)")
            return True
        except Exception as e2:
            LOGGER.error(f"[PmSender] Fallback send also failed: {e2}")
            return False

    except Exception as e:
        LOGGER.error(f"[PmSender] send_video failed uid={user_id}: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONVENIENCE WRAPPER — used when you only need the cover bytes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def get_cover_bytes(title: str, year: str | int | None = None) -> bytes | None:
    """
    Fetch and return JPEG bytes of the movie poster (1280×720, full quality).
    Use this when you need the raw bytes for a custom send flow.
    """
    return await fetch_movie_poster(title, year)


async def save_cover_to_file(
    title: str,
    year: str | int | None = None,
    dest: str | None = None,
) -> str | None:
    """
    Fetch the movie poster and save to a file.
    Returns the file path if successful, None otherwise.
    """
    from bot.utils.thumb_store import TMP_DIR
    out = dest or os.path.join(TMP_DIR, f"cover_{int(time.time())}.jpg")
    data = await fetch_movie_poster(title, year)
    if not data:
        return None
    try:
        async with aiofiles.open(out, "wb") as f:
            await f.write(data)
        return out
    except Exception as e:
        LOGGER.error(f"[PmSender] save_cover_to_file: {e}")
        return None
