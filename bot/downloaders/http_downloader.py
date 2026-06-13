"""
HTTP/HTTPS Downloader — NXTL
Handles direct HTTP/HTTPS streaming downloads with progress tracking.
"""
import os
import re
import time
import asyncio
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


async def http_download(url: str, dest_dir: str, task_id: str, msg) -> str:
    """
    Download a file over HTTP/HTTPS with progress reporting.
    Returns the local path of the downloaded file.
    """
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
