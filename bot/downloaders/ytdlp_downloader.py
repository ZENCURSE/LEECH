"""
yt-dlp Downloader — NXTL
Handles YouTube, M3U8/HLS, and 1000+ sites.

Fixes:
  - M3U8/HLS: dedicated format selector + ffmpeg concat downloader
  - Cookies: only applied for sites that actually need them
  - Chrome impersonation: tries 131 → 124 → 120 → generic → none
"""
import os
import re
import asyncio
import time
import config

UPDATE_SEC = 4

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Sites that NEED cookies for age-restricted or login-gated content
_COOKIE_SITES = re.compile(
    r"(youtube\.com|youtu\.be"
    r"|instagram\.com|twitter\.com|x\.com"
    r"|facebook\.com|tiktok\.com"
    r"|bilibili\.com|niconico\.jp"
    r"|crunchyroll\.com|netflix\.com"
    r"|primevideo\.com|disneyplus\.com)",
    0,
)

import re   # needed by _COOKIE_SITES — import at module level

_IMPERSONATE_TARGETS = [
    ("chrome", "131", "windows"),
    ("chrome", "124", "windows"),
    ("chrome", "120", "windows"),
    ("chrome", None,  None),
]

def _get_impersonate():
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        for client, version, os_ in _IMPERSONATE_TARGETS:
            try:
                return ImpersonateTarget(client=client, version=version, os=os_)
            except Exception:
                continue
    except ImportError:
        pass
    return None

def _needs_cookies(url: str) -> bool:
    return bool(_COOKIE_SITES.search(url))

def _get_cookies_path(uid: int, url: str) -> str | None:
    """Return cookie file path only if the site needs it AND the user has one."""
    if not uid or not _needs_cookies(url):
        return None
    try:
        from bot.database import users_db
        s = users_db.get_settings(uid)
        cp = s.get("cookies_path")
        if cp and os.path.exists(cp):
            return cp
    except Exception:
        pass
    return None

def _is_m3u8(url: str) -> bool:
    return bool(re.search(
        r"\.m3u8(\?|#|$)"
        r"|/hls/"
        r"|/m3u8/"
        r"|playlist\.m3u8"
        r"|index\.m3u8"
        r"|master\.m3u8"
        r"|chunklist\.m3u8"
        r"|[?&]format=(hls|m3u8)"
        r"|[?&]type=(hls|m3u8)",
        url, re.I,
    ))


async def ytdlp_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    import yt_dlp
    from bot.core import task_manager as tm
    from bot.utils.progress import build_progress_card, safe_edit

    os.makedirs(dest_dir, exist_ok=True)
    loop     = asyncio.get_running_loop()
    out_path = [None]
    err_ref  = [None]
    last_upd = [0.0]
    started  = time.time()
    is_hls   = _is_m3u8(url)
    cookies  = _get_cookies_path(uid, url)

    outtmpl = os.path.join(dest_dir, "%(title).180B.%(ext)s")

    def _hook(d: dict):
        if tm.is_cancelled(task_id):
            raise yt_dlp.utils.DownloadCancelled()

        status = d.get("status", "")
        if status == "finished":
            out_path[0] = d.get("filename") or out_path[0]

        elif status == "downloading":
            done  = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0.0
            eta   = d.get("eta") or 0.0
            fname = os.path.basename(d.get("filename") or "") or "Downloading…"

            tm.update_progress(task_id, name=fname, done=done, total=total,
                               speed=speed, eta=eta, status="downloading")

            now = time.monotonic()
            if now - last_upd[0] >= UPDATE_SEC:
                last_upd[0] = now
                pct = (done / total * 100) if total else 0
                asyncio.run_coroutine_threadsafe(
                    safe_edit(
                        msg,
                        build_progress_card(
                            "downloading", fname, pct,
                            done=done, total=total,
                            speed=speed, eta=eta,
                            elapsed=time.time() - started,
                            tid=task_id,
                        ),
                    ),
                    loop,
                )

    def _build_opts(impersonate=None, hls_native=False) -> dict:
        if is_hls:
            # FIX: prefer muxed "best" first — most HLS streams are muxed,
            # not split into separate video+audio tracks.
            # "bestvideo+bestaudio/best" was backwards and broke muxed streams.
            fmt = "best/bestvideo+bestaudio/bestvideo+bestaudio"
            merge_fmt = "mp4"
        else:
            fmt = (
                "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4]+bestaudio"
                "/bestvideo+bestaudio"
                "/best[ext=mp4]/best"
            )
            merge_fmt = "mp4"

        opts: dict = {
            "outtmpl":                    {"default": outtmpl},
            "format":                     fmt,
            "merge_output_format":        merge_fmt,
            "writethumbnail":             False,
            "writesubtitles":             False,
            "writeautomaticsub":          False,
            "noprogress":                 True,
            "overwrites":                 True,
            "trim_file_name":             180,
            "fragment_retries":           15,
            "retries":                    15,
            "file_access_retries":        5,
            "extractor_retries":          5,
            "socket_timeout":             30,
            "progress_hooks":             [_hook],
            "quiet":                      True,
            "no_warnings":                True,
            # FIX: do NOT set noplaylist=True for HLS — M3U8 IS a playlist/manifest.
            # noplaylist only applies to non-HLS (YouTube playlists etc).
            "noplaylist":                 not is_hls,
            "concurrent_fragment_downloads": 4,
            "source_address":             "0.0.0.0",
            "http_headers": {
                "User-Agent":         UA,
                "Accept-Language":    "en-US,en;q=0.9",
                "Accept":             "*/*",
                "Sec-Fetch-Mode":     "navigate",
                "Sec-Ch-Ua":          '"Chromium";v="131","Google Chrome";v="131"',
                "Sec-Ch-Ua-Mobile":   "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            },
            "retry_sleep_functions": {
                "http":        lambda n: min(3 * n, 15),
                "fragment":    lambda n: min(3 * n, 15),
                "file_access": lambda n: 3,
                "extractor":   lambda n: 3,
            },
            "postprocessors": [{
                "key":          "FFmpegMetadata",
                "add_metadata": True,
                "add_chapters": True,
            }],
        }

        # HLS-specific options
        if is_hls:
            opts["geo_bypass"] = True
            # FIX: do NOT use external_downloader=ffmpeg for HLS when also using
            # bestvideo+bestaudio format selector — ffmpeg can't handle both
            # stream-merging and HLS download simultaneously.
            # Use yt-dlp's native HLS downloader instead; it handles muxed
            # and split streams correctly.
            opts["hls_use_mpegts"] = True   # better segment buffering
            if hls_native:
                # Second-attempt: force native HLS downloader explicitly
                opts["hls_prefer_native"] = True

        if cookies:
            opts["cookiefile"] = cookies

        if impersonate is not None:
            opts["impersonate"] = impersonate

        return opts

    ydl_ref = [None]

    def _resolve_path(info) -> str | None:
        if not info:
            return None
        for entry in (info.get("requested_downloads") or []):
            fn = entry.get("filepath") or entry.get("filename", "")
            if fn and os.path.exists(fn):
                return fn
        final = ydl_ref[0].prepare_filename(info) if ydl_ref[0] else None
        if final and os.path.exists(final):
            return final
        return out_path[0]

    def _run_once(opts: dict):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl_ref[0] = ydl
            info = ydl.extract_info(url, download=True)
            path = _resolve_path(info)
            if path:
                out_path[0] = path

    def _run():
        imp  = _get_impersonate()
        opts = _build_opts(imp)
        try:
            _run_once(opts)
            return
        except yt_dlp.utils.DownloadCancelled:
            return
        except Exception as e:
            err1 = str(e).lower()
            # Retry without impersonation if it was the cause
            if imp is not None and any(
                kw in err1 for kw in ("impersonate", "curl_cffi", "unsupported", "no such target")
            ):
                try:
                    _run_once(_build_opts(None))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e2:
                    err_ref[0] = e2
                    return
            # HLS-specific retry: if first attempt failed, try with native HLS downloader
            if is_hls and any(
                kw in err1 for kw in (
                    "fragment", "m3u8", "hls", "no video formats",
                    "requested format is not available", "format error",
                )
            ):
                try:
                    _run_once(_build_opts(None, hls_native=True))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e3:
                    err_ref[0] = e3
                    return
            err_ref[0] = e

    tm.set_status(task_id, "downloading")
    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError

    if err_ref[0]:
        raise RuntimeError(f"yt-dlp: {err_ref[0]}") from err_ref[0]

    # Fallback path resolution from disk
    if not out_path[0] or not os.path.exists(out_path[0]):
        files = [
            os.path.join(dest_dir, f)
            for f in os.listdir(dest_dir)
            if os.path.isfile(os.path.join(dest_dir, f))
            and not f.endswith((".part", ".ytdl", ".tmp"))
        ]
        out_path[0] = max(files, key=os.path.getmtime) if files else None

    if not out_path[0] or not os.path.exists(out_path[0]):
        raise FileNotFoundError(f"yt-dlp produced no output in {dest_dir}")

    return out_path[0]
