"""
yt-dlp Downloader — NXTL
Handles YouTube, M3U8/HLS, DASH, and 1000+ sites.

Retry chain (per attempt):
  1. With impersonation (Chrome 131/124/120)
  2. Without impersonation (if impersonation caused failure)
  3. With format=best (if bestvideo+bestaudio not available)
  4. HLS native downloader (if HLS fragment/format error)
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

# Sites that need cookies for age-restricted / login-gated content
_COOKIE_SITES = re.compile(
    r"(youtube\.com|youtu\.be"
    r"|instagram\.com|twitter\.com|x\.com"
    r"|facebook\.com|tiktok\.com"
    r"|bilibili\.com|niconico\.jp"
    r"|crunchyroll\.com|netflix\.com"
    r"|primevideo\.com|disneyplus\.com)",
    re.I,
)

_IMPERSONATE_TARGETS = [
    ("chrome", "131", "windows"),
    ("chrome", "124", "windows"),
    ("chrome", "120", "windows"),
    ("chrome", None,  None),
]

# Keywords that indicate impersonation caused the failure
_IMP_ERR_KW = ("impersonate", "curl_cffi", "unsupported", "no such target")

# Keywords that trigger HLS native-downloader retry
_HLS_ERR_KW = (
    "fragment", "m3u8", "hls",
    "no video formats found",
    "requested format is not available",
    "there is no media",
    "unable to download",
    "403", "404",
)

# Keywords that trigger format=best fallback
_FMT_ERR_KW = (
    "no video formats found",
    "requested format is not available",
    "format is not available",
    "no formats found",
    "no suitable formats",
)


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

    # FIX: %(title,id) — use video ID as fallback when title is unavailable,
    # prevents "NA.mp4" filenames on sites that don't expose a title.
    outtmpl = os.path.join(dest_dir, "%(title,id).180B.%(ext)s")

    def _hook(d: dict):
        if tm.is_cancelled(task_id):
            raise yt_dlp.utils.DownloadCancelled()

        status = d.get("status", "")
        if status == "finished":
            # FIX: check both "filename" and "filepath" — post-merge yt-dlp
            # stores the final merged path in "filepath", not "filename".
            fn = d.get("filepath") or d.get("filename")
            if fn:
                out_path[0] = fn

        elif status == "downloading":
            done  = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0.0
            eta   = d.get("eta") or 0.0
            fname = os.path.basename(d.get("filename") or "") or "Downloading…"

            # FIX: HLS streams have unknown total_bytes — fall back to
            # fragment count so the progress bar shows something useful.
            if is_hls and not total:
                frag_idx   = d.get("fragment_index") or 0
                frag_count = d.get("fragment_count") or 0
                if frag_count:
                    done  = frag_idx
                    total = frag_count

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

    def _build_opts(impersonate=None, fmt_override=None, hls_native=False) -> dict:
        if fmt_override:
            fmt = fmt_override
        elif is_hls:
            # FIX: prefer muxed "best" first — most HLS streams are muxed
            # (combined video+audio). Old "bestvideo+bestaudio/best" tried to
            # split first, which failed on the majority of HLS sources.
            fmt = "best/bestvideo+bestaudio/bestvideo+bestaudio"
        else:
            fmt = (
                "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4]+bestaudio"
                "/bestvideo+bestaudio"
                "/best[ext=mp4]/best"
            )

        opts: dict = {
            "outtmpl":                       {"default": outtmpl},
            "format":                        fmt,
            "merge_output_format":           "mp4",
            "writethumbnail":                False,
            "writesubtitles":                False,
            "writeautomaticsub":             False,
            "writedescription":              False,   # prevents .description junk files
            "writeinfojson":                 False,   # prevents .info.json junk files
            "noprogress":                    True,
            "overwrites":                    True,
            "trim_file_name":                180,
            "fragment_retries":              15,
            "retries":                       15,
            "file_access_retries":           5,
            "extractor_retries":             5,
            # FIX: was 30 — too short for large HLS manifests or slow servers
            "socket_timeout":                60,
            "progress_hooks":                [_hook],
            "quiet":                         True,
            "no_warnings":                   True,
            # FIX: noplaylist must be False for HLS — M3U8 IS a playlist/manifest.
            # For everything else keep True to avoid accidentally grabbing playlists.
            "noplaylist":                    not is_hls,
            "concurrent_fragment_downloads": 4,
            # FIX: geo_bypass is now global, not just HLS — YouTube, Vimeo, etc.
            # can also be geo-restricted.
            "geo_bypass":                    True,
            "source_address":                "0.0.0.0",
            # FIX: keepvideo=False removes intermediate .webm/.m4a streams after
            # merge so dest_dir doesn't fill up with extra files.
            "keepvideo":                     False,
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

        if is_hls:
            opts["hls_use_mpegts"]          = True   # better segment buffering
            # FIX: handle streams with discontinuities (ad breaks, restarts)
            opts["hls_split_discontinuity"] = True
            if hls_native:
                # Retry pass: force yt-dlp's native HLS downloader
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
        # 1. requested_downloads has the final merged filepath
        for entry in (info.get("requested_downloads") or []):
            for key in ("filepath", "filename"):
                fn = entry.get(key, "")
                if fn and os.path.exists(fn):
                    return fn
        # 2. prepare_filename — gives pre-merge name; also probe merged extensions
        #    because after merging .webm+.m4a → .mp4, prepare_filename still
        #    returns ".webm" but the real file is ".mp4".
        if ydl_ref[0]:
            try:
                fn = ydl_ref[0].prepare_filename(info)
                if fn and os.path.exists(fn):
                    return fn
                base = os.path.splitext(fn)[0]
                for ext in (".mp4", ".mkv", ".webm", ".m4a", ".mp3"):
                    candidate = base + ext
                    if os.path.exists(candidate):
                        return candidate
            except Exception:
                pass
        return out_path[0]

    def _run_once(opts: dict):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl_ref[0] = ydl
            info = ydl.extract_info(url, download=True)
            # FIX: guard against silent None return from extract_info
            if info is None:
                raise RuntimeError("yt-dlp returned no info — download may have failed silently")
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

            # ── Retry 1: impersonation caused failure → drop it ──────────
            if imp is not None and any(kw in err1 for kw in _IMP_ERR_KW):
                try:
                    _run_once(_build_opts(None))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e2:
                    err_ref[0] = e2
                    return

            # ── Retry 2: format not available → fall back to best ────────
            # Catches: private/geo/age-restricted where bestvideo+bestaudio
            # isn't available but "best" muxed still works.
            if any(kw in err1 for kw in _FMT_ERR_KW):
                try:
                    _run_once(_build_opts(None, fmt_override="best"))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e3:
                    err_ref[0] = e3
                    # fall through to HLS retry if applicable
                    err1 = str(e3).lower()

            # ── Retry 3: HLS-specific → native yt-dlp HLS downloader ────
            if is_hls and any(kw in err1 for kw in _HLS_ERR_KW):
                try:
                    _run_once(_build_opts(None, hls_native=True))
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e4:
                    err_ref[0] = e4
                    return

            if not err_ref[0]:
                err_ref[0] = e

    tm.set_status(task_id, "downloading")
    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError

    if err_ref[0]:
        # FIX: include URL in error so logs show which link failed
        raise RuntimeError(
            f"yt-dlp failed [{url[:80]}]: {err_ref[0]}"
        ) from err_ref[0]

    # Fallback path resolution from disk (catches merged/renamed files that
    # hook and _resolve_path both missed)
    if not out_path[0] or not os.path.exists(out_path[0]):
        files = [
            os.path.join(dest_dir, f)
            for f in os.listdir(dest_dir)
            if os.path.isfile(os.path.join(dest_dir, f))
            # FIX: also exclude .json and .description — yt-dlp metadata files
            and not f.endswith((".part", ".ytdl", ".tmp", ".json", ".description"))
        ]
        out_path[0] = max(files, key=os.path.getmtime) if files else None

    if not out_path[0] or not os.path.exists(out_path[0]):
        raise FileNotFoundError(f"yt-dlp produced no output in {dest_dir}")

    return out_path[0]
