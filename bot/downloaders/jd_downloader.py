"""
JDownloader Downloader — NXTL
Talks to locally-running JDownloader via myjd local API (127.0.0.1:3128).
Only needs JD_EMAIL + JD_PASS — no device selection required.
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

    # Ensure JD is running
    if not jdownloader.is_alive():
        await safe_edit(msg, _card(task_id, "🚀 Starting JDownloader…"))
        await jdownloader.boot()
        if not jdownloader.is_alive():
            raise RuntimeError(f"JDownloader unavailable: {jdownloader.error}")

    dev = jdownloader.device

    # ── 1. Clear linkgrabber & add URL ───────────────────────
    await safe_edit(msg, _card(task_id, "🔍 Adding link to JDownloader…"))
    try:
        await dev.linkgrabber.clear_list()
    except Exception:
        pass

    await dev.linkgrabber.add_links([{
        "autoExtract": False,
        "links": url,
        "destinationFolder": dest_dir,
        "overwritePackagizerRules": True,
    }])

    # ── 2. Wait for collection ────────────────────────────────
    await safe_edit(msg, _card(task_id, "🧲 Collecting link info…"))
    for _ in range(30):
        await asyncio.sleep(1)
        try:
            if not await dev.linkgrabber.is_collecting():
                break
        except Exception:
            break

    # ── 3. Query packages ─────────────────────────────────────
    packages = []
    for _ in range(20):
        await asyncio.sleep(1)
        try:
            pkgs = await dev.linkgrabber.query_packages(
                [{"bytesTotal": True, "saveTo": True, "name": True}]
            ) or []
            if pkgs:
                packages = pkgs
                break
        except Exception:
            continue

    if not packages:
        raise RuntimeError("JDownloader: No packages found — link may be invalid or offline")

    pkg_ids    = [p["uuid"] for p in packages]
    total_size = sum(p.get("bytesTotal", 0) for p in packages)
    name       = packages[0].get("name", f"jd_{task_id}")

    tm.update_progress(task_id, name=name, done=0, total=total_size, status="downloading")

    # ── 4. Move to download list & force start ────────────────
    await safe_edit(msg, _card(task_id, f"⬇️ Starting: {name}"))
    await dev.linkgrabber.move_to_downloadlist([], pkg_ids)
    await asyncio.sleep(1)
    await dev.downloads.force_download([], pkg_ids)
    tm.set_status(task_id, "downloading")

    # ── 5. Poll progress ──────────────────────────────────────
    started   = time.monotonic()
    last_done = 0
    last_t    = time.monotonic()

    while True:
        if tm.is_cancelled(task_id):
            try:
                await dev.downloads.remove_links([], pkg_ids)
            except Exception:
                pass
            raise asyncio.CancelledError

        await asyncio.sleep(4)

        try:
            dl_pkgs = await dev.downloads.query_packages([{
                "bytesLoaded": True, "bytesTotal": True,
                "name": True, "finished": True, "status": True,
            }]) or []
        except Exception as e:
            LOGGER.warning(f"[JD] poll error: {e}")
            if not jdownloader.is_alive():
                raise RuntimeError("JDownloader connection lost during download")
            continue

        matching = [p for p in dl_pkgs if p.get("uuid") in pkg_ids] or dl_pkgs

        done  = sum(p.get("bytesLoaded", 0)  for p in matching)
        total = sum(p.get("bytesTotal",  0)   for p in matching) or total_size or 1
        now   = time.monotonic()
        dt    = now - last_t
        speed = (done - last_done) / dt if dt > 0 and done >= last_done else 0.0
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
                user_mention=tm.get_user_mention(task_id),
            ),
        )

        if all(p.get("finished", False) for p in matching) and matching:
            break

        # 4h timeout
        if now - started > 14400:
            raise RuntimeError("JDownloader: download timed out (4h)")

    # ── 6. Find result ────────────────────────────────────────
    result = _find(dest_dir)
    if not result:
        await asyncio.sleep(3)
        result = _find(dest_dir)
    if not result:
        raise FileNotFoundError(f"JDownloader finished but no files found in {dest_dir}")

    LOGGER.info(f"[JD] ✅ {result}")
    return result


def _find(d: str) -> str | None:
    skip = (".part", ".tmp", ".!qB", ".crdownload", ".incomplete")
    files = []
    for root, _, fs in os.walk(d):
        for f in fs:
            if not any(f.endswith(s) for s in skip):
                files.append(os.path.join(root, f))
    if not files:
        return None
    return files[0] if len(files) == 1 else d


def _card(tid: str, step: str) -> str:
    wm = getattr(config, "WATERMARK", "@NXT_HUB")
    return (
        f"╔═「 🔗 <b>JDOWNLOADER</b> 」\n"
        f"║\n"
        f"║  ➤ {step}\n"
        f"║  ➤ <b>Task</b> : <code>#{tid}</code>\n"
        f"╚══════════════════════\n"
        f"  <i>{wm}</i>"
    )
