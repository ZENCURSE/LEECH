"""
yt-dlp Downloader — NXTL
Handles YouTube, M3U8, and 1000+ sites via yt-dlp.
Pinned to yt-dlp>=2026.06.09 (CVE fixes, Chrome impersonation fix #16440).
"""
import os
import asyncio
import time
import config

UPDATE_SEC = 4

# Chrome 131 UA — matches the 131 impersonation target below
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _build_impersonate():
    """
    Return a valid ImpersonateTarget or None.
    curl_cffi >= 0.7 and yt-dlp >= 2026.06.09 required for impersonation.
    Falls back gracefully if not available or target unsupported.
    """
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        # Use a specific pinned Chrome version to avoid 'unsupported target' errors.
        # yt-dlp #16440 fixed target resolution — chrome-131 is broadly supported.
        return ImpersonateTarget(client="chrome", version="131", os="windows")
    except Exception:
        return None


async def ytdlp_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    """
    Download via yt-dlp. Returns local path of downloaded file.
    """
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

    outtmpl  = os.path.join(dest_dir, "%(title).200B.%(ext)s")
    is_m3u8  = ".m3u8" in url.lower() or "m3u8" in url.lower()

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

    impersonate = _build_impersonate()

    opts: dict = {
        "outtmpl":           {"default": outtmpl},
        "format":            (
            "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
            "/bestvideo[ext=mp4]+bestaudio"
            "/bestvideo+bestaudio"
            "/best[ext=mp4]/best"
        ) if not is_m3u8 else "best",
        "merge_output_format": "mp4" if not is_m3u8 else "mkv",
        "writethumbnail":    False,
        "writesubtitles":    False,
        "writeautomaticsub": False,
        "noprogress":        True,
        "overwrites":        True,
        "trim_file_name":    200,
        "fragment_retries":  12,
        "retries":           12,
        "file_access_retries": 5,
        "extractor_retries": 5,
        "socket_timeout":    30,
        "progress_hooks":    [_hook],
        "quiet":             True,
        "no_warnings":       True,
        "noplaylist":        True,
        # Concurrent fragments — native downloader only (aria2c removed in 2026.06.09)
        "concurrent_fragment_downloads": 4,
        "http_headers": {
            "User-Agent":      UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Mode":  "navigate",
            "Sec-Ch-Ua":       '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        },
        "source_address":   "0.0.0.0",
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

    # Chrome impersonation (requires curl_cffi; silently skipped if unavailable)
    if impersonate is not None:
        opts["impersonate"] = impersonate

    # Per-user cookie file
    if uid:
        try:
            from bot.database import users_db as _udb
            s  = _udb.get_settings(uid)
            cp = s.get("cookies_path")
            if cp and os.path.exists(cp):
                opts["cookiefile"] = cp
        except Exception:
            pass

    tm.set_status(task_id, "downloading")

    def _run():
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    final = ydl.prepare_filename(info)
                    if os.path.exists(final):
                        out_path[0] = final
                    for entry in (info.get("requested_downloads") or []):
                        fn = entry.get("filepath") or entry.get("filename", "")
                        if fn and os.path.exists(fn):
                            out_path[0] = fn
                            break
        except yt_dlp.utils.DownloadCancelled:
            pass
        except yt_dlp.utils.ExtractorError as e:
            # ImpersonateTarget not supported by this build → retry without it
            if "impersonate" in str(e).lower() or "curl_cffi" in str(e).lower():
                opts.pop("impersonate", None)
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if info:
                            final = ydl.prepare_filename(info)
                            if os.path.exists(final):
                                out_path[0] = final
                except yt_dlp.utils.DownloadCancelled:
                    pass
                except Exception as e2:
                    err_ref[0] = e2
            else:
                err_ref[0] = e
        except Exception as e:
            err_ref[0] = e

    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError

    if err_ref[0]:
        raise RuntimeError(f"yt-dlp: {err_ref[0]}") from err_ref[0]

    # Resolve output path from disk if hook missed it
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
