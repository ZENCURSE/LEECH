"""
tmdb_meta.py — real movie/show metadata for the leech-bot Magic Thumbnail.

Given a (title, year) parsed from a filename, this finds the TMDB entry
and returns everything render_leech_tmdb_thumb() needs: overview, rating,
genres, runtime, age rating + Rotten Tomatoes (via OMDB), and URLs for a
real poster + backdrop image. No results found → caller falls back to the
frame-extracted card (magic_card.py).
"""

import aiohttp

import config

_TMDB    = "https://api.themoviedb.org/3"
_ORIG    = "https://image.tmdb.org/t/p/original"
_W1280   = "https://image.tmdb.org/t/p/w1280"
_W780    = "https://image.tmdb.org/t/p/w780"
_OMDB    = "https://www.omdbapi.com/"
_UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5)


async def _get_json(session, url, params=None) -> dict:
    try:
        async with session.get(url, params=params, headers={"User-Agent": _UA},
                               timeout=_TIMEOUT) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return {}


def _fmt_runtime(minutes) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m}m" if h else f"{m}m"


async def _search(session, title: str, year) -> tuple:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    if not key or not title:
        return None, "movie"
    for mtype in ("movie", "tv"):
        params = {"api_key": key, "query": title, "include_adult": "false"}
        if year:
            params["year" if mtype == "movie" else "first_air_date_year"] = year
        data = await _get_json(session, f"{_TMDB}/search/{mtype}", params)
        results = [r for r in data.get("results", []) if r.get("id")]
        if results:
            return results[0]["id"], mtype
    # No year match — retry without year constraint (year is often wrong
    # in scene filenames, e.g. re-release/dub year vs actual release year)
    if year:
        for mtype in ("movie", "tv"):
            params = {"api_key": key, "query": title, "include_adult": "false"}
            data = await _get_json(session, f"{_TMDB}/search/{mtype}", params)
            results = [r for r in data.get("results", []) if r.get("id")]
            if results:
                return results[0]["id"], mtype
    return None, "movie"


async def _omdb_ratings(session, imdb_id: str) -> dict:
    key = getattr(config, "OMDB_API_KEY", "").strip()
    if not key or not imdb_id:
        return {"age_rating": "", "rotten_tomatoes": ""}
    data = await _get_json(session, _OMDB, {"apikey": key, "i": imdb_id, "plot": "short"})
    if not data:
        return {"age_rating": "", "rotten_tomatoes": ""}
    rated = data.get("Rated") or ""
    age_rating = rated if rated and rated != "N/A" else ""
    rotten_tomatoes = ""
    for entry in data.get("Ratings", []):
        if entry.get("Source") == "Rotten Tomatoes":
            val = entry.get("Value") or ""
            if val and val != "N/A":
                rotten_tomatoes = val
            break
    return {"age_rating": age_rating, "rotten_tomatoes": rotten_tomatoes}


async def _best_backdrop_url(session, tmdb_id, mtype) -> str:
    key = getattr(config, "TMDB_API_KEY", "").strip()
    data = await _get_json(session, f"{_TMDB}/{mtype}/{tmdb_id}/images",
                           {"api_key": key, "include_image_language": "en,null"})
    backdrops = sorted(
        data.get("backdrops", []) or [],
        key=lambda x: (float(x.get("vote_average", 0) or 0), int(x.get("vote_count", 0) or 0)),
        reverse=True,
    )
    if backdrops:
        return _ORIG + backdrops[0]["file_path"]
    return ""


async def fetch_metadata(title: str, year: str | None) -> dict | None:
    """
    Returns None if no TMDB match found, otherwise:
    {title, overview, rating, genres, runtime, year, age_rating,
     rotten_tomatoes, media_type, poster_url, backdrop_url}
    """
    async with aiohttp.ClientSession() as session:
        tmdb_id, mtype = await _search(session, title, year)
        if not tmdb_id:
            return None

        ep = "movie" if mtype == "movie" else "tv"
        data = await _get_json(session, f"{_TMDB}/{ep}/{tmdb_id}", {"api_key": getattr(config, "TMDB_API_KEY", "")})
        if not data:
            return None

        poster_path = data.get("poster_path")
        date  = data.get("release_date") or data.get("first_air_date") or ""
        y     = date.split("-")[0] if date else (year or "")
        genres = [g["name"] for g in data.get("genres", []) if g.get("name")]
        real_title = data.get("title") or data.get("name") or title

        if mtype == "movie":
            runtime = _fmt_runtime(data.get("runtime") or 0)
        else:
            ep_runtimes = data.get("episode_run_time") or []
            runtime = f"{_fmt_runtime(ep_runtimes[0])}/ep" if ep_runtimes and ep_runtimes[0] else ""

        ext = await _get_json(session, f"{_TMDB}/{ep}/{tmdb_id}/external_ids",
                              {"api_key": getattr(config, "TMDB_API_KEY", "")})
        imdb_id = ext.get("imdb_id", "")
        ratings = await _omdb_ratings(session, imdb_id) if imdb_id else \
            {"age_rating": "", "rotten_tomatoes": ""}

        backdrop_url = await _best_backdrop_url(session, tmdb_id, ep)

        return {
            "title":           real_title,
            "overview":        data.get("overview", "") or "",
            "rating":          round(data.get("vote_average", 0.0) or 0.0, 1),
            "genres":          genres,
            "runtime":         runtime,
            "year":            y,
            "age_rating":      ratings["age_rating"],
            "rotten_tomatoes": ratings["rotten_tomatoes"],
            "media_type":      mtype,
            "poster_url":      f"{_W780}{poster_path}" if poster_path else "",
            "backdrop_url":    backdrop_url,
        }
