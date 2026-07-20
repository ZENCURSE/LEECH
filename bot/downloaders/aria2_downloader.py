"""
aria2_downloader.py — HTTP and torrent/magnet downloads via the aria2c
daemon (JSON-RPC), replacing:
  - http_downloader.py's single-connection aiohttp streaming
  - qbt_downloader.py's qBittorrent dependency for torrents

aria2c is started in main.py with split=16 / max-connection-per-server=16
(aria2's own hard cap), so every HTTP download is pulled over up to 16
parallel connections instead of one — real multi-threaded speed instead
of a single TCP stream.
"""

import asyncio
import os
import time

import config
from bot.core import task_manager as tm
from bot.utils import aria2_client as ar2
from bot.utils.progress import build_progress_card, task_kb, safe_edit

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_POLL_SEC   = 2
_TIMEOUT_S  = 4 * 3600  # 4h ceiling, same as the old qBittorrent path


def _find_result(dest_dir: str) -> str | None:
    files = []
    for root, _, fs in os.walk(dest_dir):
        for f in fs:
            if f.endswith((".aria2", ".!qB", ".parts")):
                continue
            p = os.path.join(root, f)
            if os.path.isfile(p):
                files.append(p)
    if not files:
        return None
    return files[0] if len(files) == 1 else dest_dir


async def _cleanup_partial(gid: str) -> None:
    try:
        await ar2.remove(gid)
    except Exception:
        pass


async def _poll_until_done(gid: str, task_id: str, msg, dest_dir: str,
                            label_prefix: str = "") -> str:
    """Shared polling loop for both HTTP and torrent downloads. Follows
    aria2's `followedBy` gid chain (magnet metadata gid → real download
    gid) automatically."""
    started   = time.monotonic()
    kb        = task_kb(task_id)
    tm.set_status(task_id, "downloading")
    tm.set_gid(task_id, gid)

    while True:
        if tm.is_cancelled(task_id):
            await _cleanup_partial(gid)
            raise asyncio.CancelledError

        try:
            st = await ar2.tell_status(gid, [
                "status", "totalLength", "completedLength", "downloadSpeed",
                "files", "bittorrent", "errorMessage", "followedBy", "dir",
            ])
        except Exception:
            await asyncio.sleep(_POLL_SEC)
            continue

        status = st.get("status")

        # Magnet metadata resolved → aria2 spawns a new gid for the real
        # download; switch our tracking to it and keep polling
        followed = st.get("followedBy") or []
        if status == "complete" and followed:
            gid = followed[0]
            tm.set_gid(task_id, gid)
            continue

        if status == "error":
            raise RuntimeError(f"aria2 error: {st.get('errorMessage', 'unknown error')}")

        total = int(st.get("totalLength") or 0)
        done  = int(st.get("completedLength") or 0)
        speed = float(st.get("downloadSpeed") or 0)
        eta   = (total - done) / speed if speed > 0 and total > done else 0
        pct   = (done / total * 100) if total else 0

        name = None
        bt = st.get("bittorrent") or {}
        if bt.get("info", {}).get("name"):
            name = bt["info"]["name"]
        elif st.get("files"):
            fp = st["files"][0].get("path", "")
            name = os.path.basename(fp) if fp else None
        name = name or label_prefix or "download"

        tm.update_progress(task_id, name=name, done=done, total=total,
                           speed=speed, eta=eta, status="downloading")
        await safe_edit(
            msg,
            build_progress_card(
                "downloading", name, pct,
                done=done, total=total, speed=speed, eta=eta,
                elapsed=time.monotonic() - started, tid=task_id,
                user_mention=tm.get_user_mention(task_id),
            ),
            kb,
        )

        if status == "complete":
            break
        if status in ("removed",):
            raise RuntimeError("aria2: download was removed")
        if time.monotonic() - started > _TIMEOUT_S:
            await _cleanup_partial(gid)
            raise RuntimeError(f"aria2: download timed out ({_TIMEOUT_S // 3600}h)")

        await asyncio.sleep(_POLL_SEC)

    result = _find_result(dest_dir)
    if not result:
        await asyncio.sleep(2)
        result = _find_result(dest_dir)
    if not result:
        raise FileNotFoundError(f"aria2 finished but no files found in {dest_dir}")
    return result


async def http_download(url: str, dest_dir: str, task_id: str, msg) -> str:
    """
    Multi-connection HTTP/HTTPS download via aria2 (up to 16 parallel
    connections per server — aria2's own hard cap). Drop-in replacement
    for the old single-connection aiohttp streamer; same signature.
    """
    os.makedirs(dest_dir, exist_ok=True)
    options = {
        "header": [f"User-Agent: {UA}"],
        "split": "16",
        "max-connection-per-server": "16",
        "min-split-size": "5M",
        "max-concurrent-downloads": "5",
        "allow-overwrite": "true",
        "auto-file-renaming": "false",
    }
    gid = await ar2.add_uri([url], dest_dir, options)
    return await _poll_until_done(gid, task_id, msg, dest_dir)


async def torrent_download(url: str, dest_dir: str, task_id: str, msg,
                            uid: int = 0) -> str:
    """
    Torrent/magnet download via aria2 — replaces qbt_download(). Same
    signature (url, dest_dir, task_id, msg, uid=0) so the dispatch sites
    in download.py don't need to change beyond the import.
    """
    os.makedirs(dest_dir, exist_ok=True)
    options = {
        "seed-time": "0",
        "bt-stop-timeout": "600",
        "max-connection-per-server": "16",
        "split": "16",
        "follow-torrent": "mem",
    }

    if url.startswith("magnet:"):
        gid = await ar2.add_uri([url], dest_dir, options)
    elif os.path.isfile(url):
        gid = await ar2.add_torrent(url, dest_dir, options)
    elif url.startswith(("http://", "https://")):
        # Remote .torrent URL (or a tracker link that serves one) — aria2's
        # addUri can't parse torrent bytes on the fly, so fetch it first,
        # then add the downloaded .torrent file
        torrent_path = await _fetch_torrent_file(url, dest_dir)
        gid = await ar2.add_torrent(torrent_path, dest_dir, options)
        try:
            os.remove(torrent_path)
        except Exception:
            pass
    else:
        raise ValueError(f"Invalid torrent source: {url}")

    return await _poll_until_done(gid, task_id, msg, dest_dir, label_prefix="torrent")


async def _fetch_torrent_file(url: str, dest_dir: str) -> str:
    """Download a .torrent file from a direct URL/tracker link so it can be
    handed to aria2.addTorrent (which needs the actual file, not a URL)."""
    import aiohttp
    headers = {"User-Agent": UA}
    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, timeout=timeout, allow_redirects=True) as r:
            if r.status != 200:
                raise RuntimeError(f"Couldn't fetch .torrent file (HTTP {r.status}): {url}")
            data = await r.read()
    if not data or data[:1] != b"d":  # bencoded dicts always start with 'd'
        raise RuntimeError(
            "That link didn't return a valid .torrent file "
            "(the tracker may require login/cookies, or the link has expired)."
        )
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"_fetched_{int(time.time())}.torrent")
    with open(path, "wb") as f:
        f.write(data)
    return path
