"""
qBittorrent Downloader — NXTL
Handles both .torrent files and magnet links.
Uses qbittorrent-api (sync, runs in executor for async compat).
"""
import asyncio
import os
import time

import config
from bot import LOGGER


def _get_client():
    import qbittorrentapi
    return qbittorrentapi.Client(
        host=getattr(config, "QBT_HOST", "localhost"),
        port=int(getattr(config, "QBT_PORT", 8090)),
        username=getattr(config, "QBT_USERNAME", "admin"),
        password=getattr(config, "QBT_PASSWORD", "adminadmin"),
        REQUESTS_ARGS={"timeout": (10, 30)},
        VERIFY_WEBUI_CERTIFICATE=False,
    )


async def qbt_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    from bot.core import task_manager as tm
    from bot.utils.progress import build_progress_card, safe_edit

    os.makedirs(dest_dir, exist_ok=True)
    loop    = asyncio.get_event_loop()
    started = time.monotonic()
    _hash   = [None]

    async def _run(fn, *a, **kw):
        return await loop.run_in_executor(None, lambda: fn(*a, **kw))

    # ── Connect ───────────────────────────────────────────────
    client = _get_client()
    try:
        await _run(client.auth_log_in)
    except Exception as e:
        raise RuntimeError(f"qBittorrent connection failed: {e}\nCheck QBT_HOST/PORT/USER/PASS in config") from e

    # ── Add torrent ───────────────────────────────────────────
    await safe_edit(msg, _status_card(task_id, "🧲 Adding torrent…"))
    try:
        if url.startswith("magnet:"):
            await _run(client.torrents_add,
                       urls=url, save_path=dest_dir,
                       use_auto_torrent_management=False,
                       is_stopped=False, tags=task_id)
        elif os.path.isfile(url):
            with open(url, "rb") as f:
                data = f.read()
            await _run(client.torrents_add,
                       torrent_files=data, save_path=dest_dir,
                       use_auto_torrent_management=False,
                       is_stopped=False, tags=task_id)
        else:
            raise ValueError(f"Invalid torrent source: {url}")
    except Exception as e:
        raise RuntimeError(f"qBittorrent add failed: {e}") from e

    # ── Wait for torrent to appear ────────────────────────────
    await asyncio.sleep(2)
    for _ in range(30):
        try:
            torrents = await _run(client.torrents_info, tag=task_id)
            if torrents:
                _hash[0] = torrents[0].hash
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not _hash[0]:
        raise RuntimeError("qBittorrent: torrent did not appear after 30s")

    th = _hash[0]
    tm.set_status(task_id, "downloading")

    # ── Poll progress ─────────────────────────────────────────
    last_done = 0
    last_t    = time.monotonic()

    while True:
        if tm.is_cancelled(task_id):
            try:
                await _run(client.torrents_delete, delete_files=True, torrent_hashes=th)
            except Exception:
                pass
            raise asyncio.CancelledError

        await asyncio.sleep(5)

        try:
            info = (await _run(client.torrents_info, torrent_hashes=th)) or []
            if not info:
                break
            t = info[0]
        except Exception as e:
            LOGGER.warning(f"[QBT] poll error: {e}")
            continue

        state = t.state
        done  = t.downloaded
        total = t.size or 1
        now   = time.monotonic()
        dt    = now - last_t
        speed = (done - last_done) / dt if dt > 0 and done > last_done else t.dlspeed or 0.0
        eta   = t.eta if t.eta and t.eta < 86400 else ((total - done) / speed if speed > 0 else 0)
        last_done, last_t = done, now

        pct  = (done / total * 100) if total else 0
        name = t.name or f"torrent_{th[:8]}"

        tm.update_progress(task_id, name=name, done=done, total=total,
                           speed=speed, eta=eta, status="downloading")
        await safe_edit(
            msg,
            build_progress_card(
                "downloading", name, pct,
                done=done, total=total,
                speed=speed, eta=eta,
                elapsed=now - started,
                tid=task_id,
            ),
        )

        # Finished states
        if state in ("uploading", "stalledUP", "checkingUP", "forcedUP",
                     "seeding", "completed", "stoppedUP"):
            break
        # Error states
        if state in ("error", "missingFiles"):
            raise RuntimeError(f"qBittorrent error state: {state} for {name}")
        # Timeout: 4 hours
        if time.monotonic() - started > 14400:
            raise RuntimeError("qBittorrent: download timed out (4h)")

    # ── Stop seeding, find files ──────────────────────────────
    try:
        await _run(client.torrents_stop, torrent_hashes=th)
    except Exception:
        pass

    result = _find_result(dest_dir)
    if not result:
        await asyncio.sleep(3)
        result = _find_result(dest_dir)

    # Clean up torrent from qBittorrent (keep files)
    try:
        await _run(client.torrents_delete, delete_files=False, torrent_hashes=th)
    except Exception:
        pass

    if not result:
        raise FileNotFoundError(f"qBittorrent finished but no files found in {dest_dir}")

    LOGGER.info(f"[QBT] Completed: {result}")
    return result


def _find_result(dest_dir: str) -> str | None:
    files = []
    for root, _, fs in os.walk(dest_dir):
        for f in fs:
            if f.endswith((".!qB", ".parts", ".fastresume")):
                continue
            p = os.path.join(root, f)
            if os.path.isfile(p):
                files.append(p)
    if not files:
        return None
    return files[0] if len(files) == 1 else dest_dir


def _status_card(tid: str, step: str) -> str:
    wm = getattr(config, "WATERMARK", "@NXT_HUB")
    return (
        f"╔═「 🌊 <b>QBITTORRENT</b> 」\n"
        f"║\n"
        f"║  ➤ {step}\n"
        f"║  ➤ <b>Task</b> : <code>#{tid}</code>\n"
        f"╚══════════════════════\n"
        f"  <i>{wm}</i>"
    )


async def resume_qbt_with_selection(gid: str):
    pass
