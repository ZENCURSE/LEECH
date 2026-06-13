"""
Download core — NXT_HUB v5

HTTP:    aiohttp streaming
yt-dlp:  Fixed YouTube + M3U8 support with proper impersonation & cookies
aria2:   JSON-RPC via aiohttp, torrent/magnet support
JDLeech: JDownloader2-style direct link resolution (ported from WZML-X jd_leech)
"""
import os
import re
import json
import asyncio
import time
import base64
import aiohttp
import aiofiles
import config

from bot.core import task_manager as tm
from bot.utils.progress import downloading_card, task_kb

CHUNK        = 512 * 1024
UPDATE_SEC   = 4
SPEED_WINDOW = 5.0
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
def _filename_from_response(r: aiohttp.ClientResponse, url: str) -> str:
    cd = r.headers.get("Content-Disposition", "")
    m  = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;\r\n]+)", cd, re.I)
    if m:
        from urllib.parse import unquote
        name = unquote(m.group(1).strip().strip("\"'"))
        if name:
            return _safe_name(name)
    m = re.search(r'filename\s*=\s*["\']?([^"\';\r\n]+)', cd, re.I)
    if m:
        name = m.group(1).strip().strip("\"'")
        if name:
            return _safe_name(name)
    from urllib.parse import urlparse, unquote
    seg = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return _safe_name(seg) if seg else "download"


def _safe_name(n: str) -> str:
    n = re.sub(r'[\\/*?:"<>|]', "_", n)
    return n.strip(". ") or "download"


async def _safe_edit(msg, text: str, kb) -> None:
    pass  # status card handles display


def _rolling_speed(buf: list, now: float, new_bytes: int):
    buf.append((now, new_bytes))
    buf = [(t, b) for t, b in buf if now - t <= SPEED_WINDOW]
    if len(buf) < 2:
        return sum(b for _, b in buf) / SPEED_WINDOW, buf
    span  = now - buf[0][0]
    speed = sum(b for _, b in buf) / max(span, 0.001)
    return speed, buf


# ─────────────────────────────────────────────
#  HTTP / HTTPS
# ─────────────────────────────────────────────
async def http_download(url: str, dest_dir: str, task_id: str, msg) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    kb      = task_kb(task_id)
    timeout = aiohttp.ClientTimeout(total=None, connect=30,
                                    sock_connect=30, sock_read=None)
    headers = {"User-Agent": UA, "Accept": "*/*"}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, timeout=timeout, allow_redirects=True) as r:
            r.raise_for_status()
            name  = _filename_from_response(r, url)
            dest  = os.path.join(dest_dir, name)
            total = int(r.headers.get("Content-Length", 0))

            tm.set_status(task_id, "downloading")
            tm.update_progress(task_id, name=name, done=0, total=total,
                               speed=0.0, eta=0.0, status="downloading")

            done      = 0
            last_edit = 0.0
            spd_buf: list = []

            async with aiofiles.open(dest, "wb") as f:
                async for chunk in r.content.iter_chunked(CHUNK):
                    if tm.is_cancelled(task_id):
                        raise asyncio.CancelledError
                    await f.write(chunk)
                    done += len(chunk)
                    now   = time.monotonic()
                    speed, spd_buf = _rolling_speed(spd_buf, now, len(chunk))
                    eta   = (total - done) / speed if speed and total > done else 0
                    tm.update_progress(task_id, name=name, done=done,
                                       total=total, speed=speed, eta=eta)
                    if now - last_edit >= UPDATE_SEC:
                        last_edit = now
                        await _safe_edit(msg,
                            downloading_card(name, done, total, speed, eta, task_id), kb)
    return dest


# ─────────────────────────────────────────────
#  yt-dlp  (Fixed: YouTube + M3U8 + impersonation)
# ─────────────────────────────────────────────
async def ytdlp_download(url: str, dest_dir: str, task_id: str, msg, uid: int = 0) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    kb        = task_kb(task_id)
    loop      = asyncio.get_running_loop()
    last_edit = [0.0]
    out_path  = [None]
    cur_name  = ["Fetching info..."]
    err_ref   = [None]

    def _hook(d: dict):
        if tm.is_cancelled(task_id):
            raise Exception("Cancelled")

        if d["status"] == "finished":
            out_path[0] = d.get("filename")

        elif d["status"] == "downloading":
            done  = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0.0
            eta   = d.get("eta") or 0.0
            fname = os.path.basename(d.get("filename") or "") or cur_name[0]
            cur_name[0] = fname

            tm.update_progress(task_id, name=fname, done=done, total=total,
                               speed=speed, eta=eta, status="downloading")

            now = time.monotonic()
            if now - last_edit[0] >= UPDATE_SEC:
                last_edit[0] = now
                asyncio.run_coroutine_threadsafe(
                    _safe_edit(msg, downloading_card(fname, done, total, speed, eta, task_id), kb),
                    loop,
                )

    outtmpl = os.path.join(dest_dir, "%(title,fulltitle,alt_title)s %(height)sp.%(ext)s")

    # Base options — ported from WZML-X with YouTube bot bypass
    opts = {
        "outtmpl":              {"default": outtmpl},
        "format": (
            "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
            "/bestvideo[ext=mp4]+bestaudio"
            "/bestvideo+bestaudio"
            "/best[ext=mp4]/best"
        ),
        "merge_output_format":  "mp4",
        "writethumbnail":       False,
        "writesubtitles":       False,
        "writeautomaticsub":    False,
        "noprogress":           True,
        "overwrites":           True,
        "trim_file_name":       220,
        "fragment_retries":     10,
        "retries":              10,
        "retry_sleep_functions": {
            "http":        lambda n: 3,
            "fragment":    lambda n: 3,
            "file_access": lambda n: 3,
            "extractor":   lambda n: 3,
        },
        "file_access_retries":  5,
        "extractor_retries":    5,
        "socket_timeout":       30,
        "progress_hooks":       [_hook],
        "quiet":                True,
        "no_warnings":          True,
        "noplaylist":           True,
        "allow_multiple_video_streams": True,
        "allow_multiple_audio_streams": True,
        # YouTube bot detection bypass — Chrome impersonation (WZML approach)
        "impersonate":          "chrome-124",
        "http_headers": {
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Mode": "navigate",
        },
        "concurrent_fragment_downloads": 4,
        "postprocessors": [
            {
                "key":            "FFmpegMetadata",
                "add_metadata":   True,
                "add_chapters":   True,
                "add_infojson":   "if_exists",
            }
        ],
        # Force IPv4 — avoids YouTube IPv6 throttling/blocks
        "source_address": "0.0.0.0",
        # usenetrc allows .netrc credentials for private sites
        "usenetrc": True,
    }

    # Inject user cookies if configured
    if uid:
        try:
            from bot.database import users_db as _udb
            s = _udb.get_settings(uid)
            cp = s.get("cookies_path")
            if cp and os.path.exists(cp):
                opts["cookiefile"] = cp
        except Exception:
            pass

    # For M3U8 streams, use different format selection
    if ".m3u8" in url.lower() or "m3u8" in url.lower():
        opts["format"] = "best"
        opts["merge_output_format"] = "mkv"

    tm.set_status(task_id, "downloading")

    def _run():
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info and not out_path[0]:
                    out_path[0] = ydl.prepare_filename(info)
        except Exception as e:
            if "Cancelled" not in str(e):
                err_ref[0] = e

    await loop.run_in_executor(None, _run)

    if tm.is_cancelled(task_id):
        raise asyncio.CancelledError
    if err_ref[0]:
        raise err_ref[0]

    # Fallback: newest file
    if not out_path[0] or not os.path.exists(out_path[0]):
        cands = [
            os.path.join(dest_dir, f)
            for f in os.listdir(dest_dir)
            if os.path.isfile(os.path.join(dest_dir, f))
        ]
        out_path[0] = max(cands, key=os.path.getmtime) if cands else None

    if not out_path[0] or not os.path.exists(out_path[0]):
        raise FileNotFoundError(f"yt-dlp produced no output in {dest_dir}")

    return out_path[0]


# ─────────────────────────────────────────────
#  JD Leech (JDownloader2-style link resolver — ported from WZML-X)
# ─────────────────────────────────────────────
async def jdleech_download(url: str, dest_dir: str, task_id: str, msg) -> str:
    """
    Multi-host download using built-in direct_link_generator extractors.
    Supports: MediaFire, PixelDrain, BuzzHeavier, GoFile, TeraBox, 1Fichier,
    KrakenFiles, WeTransfer, OneDrive, Yandex, Streamtape, DoodStream,
    FileLions/StreamWish, UploadHaven, DevUploads, and more.
    No external credentials needed.
    """
    from bot.utils.direct_link_generator import generate_direct_link

    loop = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(None, generate_direct_link, url)
    except Exception as e:
        raise RuntimeError(f"JDLeech: Could not resolve link — {e}") from e

    if isinstance(result, dict) and "contents" in result:
        paths = []
        for item in result["contents"]:
            item_url = item.get("url") or item.get("direct_link", "")
            if not item_url:
                continue
            try:
                p = await http_download(item_url, dest_dir, task_id, msg)
                paths.append(p)
            except Exception:
                pass
        if paths:
            return paths[0] if len(paths) == 1 else dest_dir
        raise RuntimeError("JDLeech: Folder resolved but no files downloaded.")

    if isinstance(result, str) and result.startswith("http"):
        return await http_download(result, dest_dir, task_id, msg)

    raise RuntimeError(f"JDLeech: Could not resolve — {url[:80]}")


# ─────────────────────────────────────────────
#  aria2 JSON-RPC (torrent/magnet support)
# ─────────────────────────────────────────────
ARIA2_URL = f"{config.ARIA2_HOST}:{config.ARIA2_PORT}/jsonrpc"

async def _aria2_rpc(method: str, params: list) -> dict:
    payload = {"jsonrpc": "2.0", "id": "nxt", "method": method, "params": params}
    if config.ARIA2_SECRET:
        params.insert(0, f"token:{config.ARIA2_SECRET}")
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as s:
        async with s.post(ARIA2_URL, json=payload, timeout=timeout) as r:
            return await r.json()


async def _aria2_add_uri(url: str, options: dict) -> str:
    res = await _aria2_rpc("aria2.addUri", [[url], options])
    return res["result"]


async def _aria2_add_torrent(path: str, options: dict) -> str:
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    encoded = base64.b64encode(data).decode()
    res = await _aria2_rpc("aria2.addTorrent", [encoded, [], options])
    return res["result"]


async def _aria2_tell_status(gid: str) -> dict:
    res = await _aria2_rpc("aria2.tellStatus", [gid])
    return res.get("result", {})


async def _aria2_unpause(gid: str) -> None:
    await _aria2_rpc("aria2.unpause", [gid])


async def _aria2_remove(gid: str) -> None:
    try:
        await _aria2_rpc("aria2.forceRemove", [gid])
    except Exception:
        pass


async def _aria2_change_option(gid: str, opts: dict) -> None:
    await _aria2_rpc("aria2.changeOption", [gid, opts])


def _aria2_name(status: dict) -> str:
    bt = status.get("bittorrent", {})
    if info := bt.get("info"):
        return info.get("name", "")
    files = status.get("files", [])
    if files:
        path = files[0].get("path", "")
        return os.path.basename(path)
    return status.get("dir", "download")


async def torrent_download(
    src: str,
    dest_dir: str,
    task_id: str,
    msg,
    is_magnet: bool = False,
    existing_gid: str | None = None,
) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    kb = task_kb(task_id)

    opts = {
        "dir":            dest_dir,
        "pause":          "true",
        "seed-time":      "0",
        "follow-torrent": "true",
        # Increase connection limits for better torrent performance
        "max-connection-per-server": "16",
        "split":          "16",
        "min-split-size": "5M",
    }

    if existing_gid:
        gid = existing_gid
    elif is_magnet:
        gid = await _aria2_add_uri(src, opts)
    else:
        gid = await _aria2_add_torrent(src, opts)

    tm.set_gid(task_id, gid)
    tm.set_status(task_id, "downloading")

    # Unpause to start downloading
    await asyncio.sleep(0.5)
    try:
        await _aria2_unpause(gid)
    except Exception:
        pass

    last_edit = 0.0

    while True:
        if tm.is_cancelled(task_id):
            await _aria2_remove(gid)
            raise asyncio.CancelledError

        await asyncio.sleep(2)

        try:
            status = await _aria2_tell_status(gid)
        except Exception:
            await asyncio.sleep(3)
            continue

        st = status.get("status", "")
        if st == "error":
            err = status.get("errorMessage", "aria2 error")
            raise RuntimeError(f"Torrent error: {err}")
        if st in ("complete", "removed"):
            break

        # Follow magnet metadata chain
        followed = status.get("followedBy", [])
        if followed:
            gid = followed[0]
            tm.set_gid(task_id, gid)
            continue

        now   = time.monotonic()
        done  = int(status.get("completedLength", 0))
        total = int(status.get("totalLength", 0))
        speed = int(status.get("downloadSpeed", 0))
        eta   = (total - done) / speed if speed and total > done else 0
        name  = _aria2_name(status) or "downloading…"
        peers = int(status.get("numSeeders", 0))

        display_name = f"{name} [{peers} peers]" if peers else name
        tm.update_progress(task_id, name=display_name, done=done, total=total,
                           speed=float(speed), eta=eta, status="downloading")

        if now - last_edit >= UPDATE_SEC:
            last_edit = now
            await _safe_edit(msg,
                downloading_card(display_name, done, total, float(speed), eta, task_id), kb)

    # Collect downloaded files
    try:
        status = await _aria2_tell_status(gid)
        paths  = [
            f["path"] for f in status.get("files", [])
            if f.get("path") and os.path.exists(f["path"])
            and not f["path"].endswith(".torrent")
        ]
    except Exception:
        paths = []

    if not paths:
        for root, _, files in os.walk(dest_dir):
            for f in files:
                p = os.path.join(root, f)
                if os.path.isfile(p) and not p.endswith(".torrent"):
                    paths.append(p)

    return paths


async def torrent_get_files(gid: str) -> list[dict]:
    try:
        status = await _aria2_tell_status(gid)
        return [
            {"index": int(f.get("index", i+1)),
             "path":  f.get("path", f"File {i+1}"),
             "size":  int(f.get("length", 0))}
            for i, f in enumerate(status.get("files", []))
        ]
    except Exception:
        return []


async def torrent_set_selected(gid: str, indices: list[int]) -> None:
    real_gid   = await torrent_get_real_gid(gid)
    select_str = ",".join(str(i) for i in sorted(indices)) if indices else "0"
    await _aria2_change_option(real_gid, {"select-file": select_str})


async def torrent_get_real_gid(gid: str) -> str:
    try:
        status   = await _aria2_tell_status(gid)
        followed = status.get("followedBy", [])
        return followed[0] if followed else gid
    except Exception:
        return gid


async def torrent_pause(gid: str) -> None:
    try:
        await _aria2_rpc("aria2.pause", [gid])
    except Exception:
        pass


async def torrent_resume(gid: str) -> None:
    await _aria2_unpause(gid)


async def torrent_remove(gid: str) -> None:
    await _aria2_remove(gid)
