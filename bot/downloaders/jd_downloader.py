"""
JDownloader Downloader — NXTL
Downloads via MyJDownloader API (myjd library).
Supports: premium hosters, multi-part, direct links.
"""
import asyncio
import os
import time
from secrets import token_hex

import config
from bot import LOGGER


async def jd_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    from bot.core import task_manager as tm
    from bot.core.jdownloader_booter import jdownloader
    from bot.utils.progress import build_progress_card, safe_edit

    os.makedirs(dest_dir, exist_ok=True)

    # Connect if needed
    if not jdownloader.is_alive():
        await safe_edit(msg, _status_card(task_id, "🔌 Connecting to JDownloader…"))
        ok = await jdownloader.connect()
        if not ok:
            raise RuntimeError(f"JDownloader not available: {jdownloader.error}")

    dev = jdownloader.device
    loop = asyncio.get_event_loop()

    async def _run(fn, *args, **kwargs):
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ── 1. Clear linkgrabber and add URL ──────────────────────
    await safe_edit(msg, _status_card(task_id, "🔍 Adding to JDownloader…"))
    gid = token_hex(6)

    try:
        await _run(dev.linkgrabber.clear_list)
    except Exception:
        pass

    await _run(
        dev.linkgrabber.add_links,
        [{
            "autoExtract": False,
            "links": url,
            "packageName": f"NXTL_{gid}",
            "destinationFolder": dest_dir,
            "overwritePackagizerRules": True,
        }]
    )

    # ── 2. Wait for link grabbing to finish ───────────────────
    await safe_edit(msg, _status_card(task_id, "🧲 Collecting link info…"))
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            collecting = await _run(dev.linkgrabber.is_collecting)
            if not collecting:
                break
        except Exception:
            break

    # ── 3. Query packages ─────────────────────────────────────
    packages = []
    for _ in range(20):
        await asyncio.sleep(1)
        try:
            pkgs = await _run(dev.linkgrabber.query_packages, [{"bytesTotal": True, "saveTo": True, "name": True}])
            packages = [p for p in (pkgs or []) if dest_dir in p.get("saveTo", "")]
            if packages:
                break
        except Exception:
            continue

    if not packages:
        # Try without path filter
        pkgs = await _run(dev.linkgrabber.query_packages, [{"bytesTotal": True, "name": True}]) or []
        packages = pkgs

    if not packages:
        raise RuntimeError("JDownloader: No packages found — link may be invalid or unsupported")

    pkg_ids = [p["uuid"] for p in packages]
    total_size = sum(p.get("bytesTotal", 0) for p in packages)
    name = packages[0].get("name", f"jd_{gid}")

    tm.update_progress(task_id, name=name, done=0, total=total_size, status="downloading")

    # ── 4. Move to download list and start ────────────────────
    await safe_edit(msg, _status_card(task_id, f"⬇️ Starting download: {name}"))
    await _run(dev.linkgrabber.move_to_downloadlist, [], pkg_ids)
    await asyncio.sleep(1)
    await _run(dev.downloads.force_download, [], pkg_ids)

    tm.set_status(task_id, "downloading")

    # ── 5. Poll download progress ─────────────────────────────
    started   = time.monotonic()
    last_done = 0
    last_t    = time.monotonic()

    while True:
        if tm.is_cancelled(task_id):
            try:
                await _run(dev.downloads.remove_links, [], pkg_ids)
            except Exception:
                pass
            raise asyncio.CancelledError

        await asyncio.sleep(4)

        try:
            dl_pkgs = await _run(
                dev.downloads.query_packages,
                [{"bytesLoaded": True, "bytesTotal": True, "name": True, "finished": True, "saveTo": True, "status": True}]
            )
        except Exception as e:
            LOGGER.warning(f"[JD] poll error: {e}")
            continue

        matching = [p for p in (dl_pkgs or []) if p.get("uuid") in pkg_ids]
        if not matching:
            matching = dl_pkgs or []

        done  = sum(p.get("bytesLoaded", 0) for p in matching)
        total = sum(p.get("bytesTotal", done) for p in matching) or total_size

        now = time.monotonic()
        dt  = now - last_t
        speed = (done - last_done) / dt if dt > 0 and done > last_done else 0.0
        eta   = (total - done) / speed if speed > 0 and total > done else 0.0
        last_done, last_t = done, now

        pct = (done / total * 100) if total else 0
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

        # Check if all finished
        all_done = all(p.get("finished", False) for p in matching)
        if all_done:
            break

        # Timeout guard: 2 hours
        if time.monotonic() - started > 7200:
            raise RuntimeError("JDownloader: download timed out (2h)")

    # ── 6. Find downloaded file ───────────────────────────────
    result = _find_result(dest_dir)
    if not result:
        # Give JD a moment to write
        await asyncio.sleep(3)
        result = _find_result(dest_dir)

    if not result:
        raise FileNotFoundError(f"JDownloader finished but no files found in {dest_dir}")

    LOGGER.info(f"[JD] Completed: {result}")
    return result


def _find_result(dest_dir: str) -> str | None:
    files = []
    for root, _, fs in os.walk(dest_dir):
        for f in fs:
            p = os.path.join(root, f)
            if os.path.isfile(p) and not f.endswith((".part", ".tmp", ".!qB", ".crdownload")):
                files.append(p)
    if not files:
        return None
    return files[0] if len(files) == 1 else dest_dir


def _status_card(tid: str, step: str) -> str:
    wm = getattr(config, "WATERMARK", "@NXT_HUB")
    return (
        f"╔═「 🔗 <b>JDOWNLOADER</b> 」\n"
        f"║\n"
        f"║  ➤ {step}\n"
        f"║  ➤ <b>Task</b> : <code>#{tid}</code>\n"
        f"╚══════════════════════\n"
        f"  <i>{wm}</i>"
    )
