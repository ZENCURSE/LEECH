"""
tmdb_magic.py — TMDB-backed Magic Thumbnail for the leech bot.

Wires together:
  - tmdb_meta.fetch_metadata()      real overview/rating/genres/poster/backdrop
  - tmdb_magic_render.make_magic_thumbnail()   the actual card renderer
    (ported as-is from Auto_thumb — same layout: title, rating badge,
    overview, age/RT/genre/runtime row, poster panel, brand row)

Returns False (no exception) whenever TMDB has no match or a download
fails, so the caller can fall back to the frame-extracted card.
"""

import os

import aiohttp

from bot.utils.tmdb_meta import fetch_metadata

_UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)


async def _download(session, url, dest) -> bool:
    if not url:
        return False
    try:
        async with session.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT) as r:
            if r.status != 200:
                return False
            data = await r.read()
        if len(data) < 4000:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


async def generate_tmdb_magic_thumb(title: str, year: str | None, dest: str,
                                     custom_channel: str = "",
                                     bot_handle: str = "") -> bool:
    """Try to build the real TMDB-driven Magic Thumbnail. Returns True and
    writes `dest` on success; False (no exception) on any miss/failure."""
    meta = await fetch_metadata(title, year)
    if not meta:
        return False

    tmp = os.path.dirname(dest) or "."
    bg_path     = os.path.join(tmp, f"tmdb_bg_{os.getpid()}_{id(dest)}.jpg")
    poster_path = os.path.join(tmp, f"tmdb_poster_{os.getpid()}_{id(dest)}.jpg")

    try:
        async with aiohttp.ClientSession() as session:
            bg_ok = await _download(session, meta["backdrop_url"], bg_path)
            if not bg_ok:
                return False
            pos_ok = await _download(session, meta["poster_url"], poster_path)
            if not pos_ok:
                # fall back to using the backdrop as the poster crop too
                import shutil
                shutil.copy(bg_path, poster_path)

        from bot.utils.tmdb_magic_render import make_magic_thumbnail
        await make_magic_thumbnail(
            bg_path, poster_path, dest,
            title=meta["title"],
            overview=meta["overview"],
            brand="NXT_HUB",
            bot_handle=bot_handle,
            media_type=meta["media_type"],
            year=meta["year"],
            rating=meta["rating"],
            genres=meta["genres"],
            runtime=meta["runtime"],
            age_rating=meta["age_rating"],
            rotten_tomatoes=meta["rotten_tomatoes"],
            custom_channel=custom_channel,
        )
        return os.path.exists(dest)
    except Exception:
        return False
    finally:
        for p in (bg_path, poster_path):
            try:
                os.remove(p)
            except Exception:
                pass
