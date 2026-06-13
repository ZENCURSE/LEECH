"""
yt-dlp Downloader — NXTL
Handles YouTube, M3U8, and 1000+ sites via yt-dlp with proper
impersonation, cookies, and progress tracking.
"""
import os
import asyncio
import time
import config


UPDATE_SEC = 4
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


async def ytdlp_download(url: str, dest_dir: str, task_id: str, msg, uid: int = 0) -> str:
    """
    Download via yt-dlp. Handles YouTube, M3U8, and 1000+ sites.
    Supports per-user cookie files.
    Returns the local path of the downloaded file.
    """
    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget

    from bot.core import task_manager as tm
    from bot.utils.progress import downloading_card, task_kb

    os.makedirs(dest_dir, exist_ok=True)
    kb       = task_kb(task_id)
    loop     = asyncio.get_running_loop()
    out_path = [None]
    err_ref  = [None]
    last_upd = [0.0]

    outtmpl = os.path.join(dest_dir, "%(title).200B.%(ext)s")

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
                asyncio.run_coroutine_threadsafe(
                    _safe_edit_stub(msg,
                        downloading_card(fname, done, total, speed, eta, task_id), kb),
                    loop,
                )

    is_m3u8 = ".m3u8" in url.lower() or "m3u8" in url.lower()

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
        "fragment_retries":  10,
        "retries":           10,
        "file_access_retries": 5,
        "extractor_retries": 5,
        "socket_timeout":    30,
        "progress_hooks":    [_hook],
        "quiet":             True,
        "no_warnings":       True,
        "noplaylist":        True,
        "concurrent_fragment_downloads": 4,
        "impersonate":       ImpersonateTarget("chrome"),
        "http_headers": {
            "User-Agent":      UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          "*/*",
            "Sec-Fetch-Mode":  "navigate",
        },
        "source_address":    "0.0.0.0",
        "postprocessors": [{
            "key":          "FFmpegMetadata",
            "add_metadata": True,
            "add_chapters": True,
        }],
        "retry_sleep_functions": {
            "http":        lambda n: min(3 * n, 15),
            "fragment":    lambda n: min(3 * n, 15),
            "file_access": lambda n: 3,
            "extractor":   lambda n: 3,
        },
    }

    # Inject per-user cookie file if available
    if uid:
        try:
            from bot.database import users_db as _udb
            s   = _udb.get_settings(uid)
            cp  = s.get("cookies_path")
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
        except Exception as e:
            err_ref[0] = e

    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError

    if err_ref[0]:
        raise RuntimeError(f"yt-dlp: {err_ref[0]}") from err_ref[0]

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


async def _safe_edit_stub(msg, text, kb):
    pass  # status card handles display
