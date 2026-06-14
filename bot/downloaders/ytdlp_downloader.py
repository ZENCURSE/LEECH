"""
yt-dlp Downloader — NXTL
Handles YouTube, M3U8, and 1000+ sites.
Chrome impersonation: tries chrome-131 → 124 → 120 → no impersonation.
"""
import os
import asyncio
import time
import config

UPDATE_SEC = 4

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Ordered list of Chrome targets to try (newest → oldest → None)
_IMPERSONATE_TARGETS = [
    ("chrome", "131", "windows"),
    ("chrome", "124", "windows"),
    ("chrome", "120", "windows"),
    ("chrome", None,  None),
]


def _get_impersonate():
    """
    Return the best available ImpersonateTarget, or None if curl_cffi missing.
    Tries targets in order and returns the first one that constructs without error.
    """
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        for client, version, os_ in _IMPERSONATE_TARGETS:
            try:
                t = ImpersonateTarget(client=client, version=version, os=os_)
                return t
            except Exception:
                continue
    except ImportError:
        pass
    return None


async def ytdlp_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    import yt_dlp
    from bot.core import task_manager as tm
    from bot.utils.progress import downloading_card, task_kb

    os.makedirs(dest_dir, exist_ok=True)
    kb       = task_kb(task_id)
    loop     = asyncio.get_running_loop()
    out_path = [None]
    err_ref  = [None]
    last_upd = [0.0]
    started  = time.time()

    outtmpl = os.path.join(dest_dir, "%(title).200B.%(ext)s")
    is_m3u8 = ".m3u8" in url.lower() or "m3u8" in url.lower()

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

            tm.update_progress(
                task_id, name=fname,
                done=done, total=total,
                speed=speed, eta=eta,
                status="downloading",
            )

            now = time.monotonic()
            if now - last_upd[0] >= UPDATE_SEC:
                last_upd[0] = now
                asyncio.run_coroutine_threadsafe(
                    _safe_edit(msg,
                        downloading_card(fname, done, total, speed, eta, task_id, started),
                        kb),
                    loop,
                )

    def _base_opts() -> dict:
        opts = {
            "outtmpl":           {"default": outtmpl},
            "format": (
                "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4]+bestaudio"
                "/bestvideo+bestaudio"
                "/best[ext=mp4]/best"
            ) if not is_m3u8 else "best",
            "merge_output_format":       "mp4" if not is_m3u8 else "mkv",
            "writethumbnail":            False,
            "writesubtitles":            False,
            "writeautomaticsub":         False,
            "noprogress":                True,
            "overwrites":                True,
            "trim_file_name":            200,
            "fragment_retries":          15,
            "retries":                   15,
            "file_access_retries":       5,
            "extractor_retries":         5,
            "socket_timeout":            30,
            "progress_hooks":            [_hook],
            "quiet":                     True,
            "no_warnings":               True,
            "noplaylist":                True,
            "concurrent_fragment_downloads": 4,
            "http_headers": {
                "User-Agent":         UA,
                "Accept-Language":    "en-US,en;q=0.9",
                "Accept":             "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Mode":     "navigate",
                "Sec-Ch-Ua":          '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="99"',
                "Sec-Ch-Ua-Mobile":   "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            },
            "source_address": "0.0.0.0",
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
        return opts

    # Per-user cookies
    if uid:
        try:
            from bot.database import users_db as _udb
            s  = _udb.get_settings(uid)
            cp = s.get("cookies_path")
            if cp and os.path.exists(cp):
                _base_opts.__defaults__ = None  # will be applied below
        except Exception:
            pass

    def _apply_cookies(opts: dict):
        if uid:
            try:
                from bot.database import users_db as _udb
                s  = _udb.get_settings(uid)
                cp = s.get("cookies_path")
                if cp and os.path.exists(cp):
                    opts["cookiefile"] = cp
            except Exception:
                pass
        return opts

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

    ydl_ref = [None]

    def _run_with_opts(opts: dict):
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl_ref[0] = ydl
            info = ydl.extract_info(url, download=True)
            path = _resolve_path(info)
            if path:
                out_path[0] = path

    def _run():
        # Attempt 1: with Chrome impersonation
        imp = _get_impersonate()
        opts = _apply_cookies(_base_opts())
        if imp is not None:
            opts["impersonate"] = imp

        try:
            _run_with_opts(opts)
            return
        except yt_dlp.utils.DownloadCancelled:
            return
        except Exception as e:
            err1 = str(e).lower()
            # If impersonation caused the error, retry without it
            if imp is not None and any(
                kw in err1 for kw in ("impersonate", "curl_cffi", "unsupported target", "no such target")
            ):
                opts2 = _apply_cookies(_base_opts())   # no impersonate key
                try:
                    _run_with_opts(opts2)
                    return
                except yt_dlp.utils.DownloadCancelled:
                    return
                except Exception as e2:
                    err_ref[0] = e2
                    return
            err_ref[0] = e

    tm.set_status(task_id, "downloading")
    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError

    if err_ref[0]:
        raise RuntimeError(f"yt-dlp: {err_ref[0]}") from err_ref[0]

    # Final path resolution from disk if hook missed it
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


async def _safe_edit(msg, text, kb):
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="html")
    except Exception:
        pass
