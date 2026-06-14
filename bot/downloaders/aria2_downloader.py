"""
Aria2 / Torrent Downloader — NXTL
Handles torrents, magnets, and HTTP(S) downloads via Aria2 JSON-RPC.
Supports file selection, pause/resume, and real-time progress.
"""
import os
import asyncio
import time
import base64
import aiohttp
import aiofiles
import config

from bot.core import task_manager as tm
from bot.utils.progress import task_kb

UPDATE_SEC = 4
ARIA2_URL  = f"{config.ARIA2_HOST}:{config.ARIA2_PORT}/jsonrpc"


# ─────────────────────────────────────────────
#  Aria2 RPC Primitives
# ─────────────────────────────────────────────

async def _aria2_rpc(method: str, params: list) -> dict:
    """Execute an Aria2 JSON-RPC call."""
    payload = {"jsonrpc": "2.0", "id": "nxt", "method": method, "params": params}
    if config.ARIA2_SECRET:
        params.insert(0, f"token:{config.ARIA2_SECRET}")
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession() as s:
        async with s.post(ARIA2_URL, json=payload, timeout=timeout) as r:
            return await r.json()


async def _aria2_add_uri(url: str, options: dict) -> str:
    """Add a URI (HTTP/magnet) to aria2 and return the GID."""
    res = await _aria2_rpc("aria2.addUri", [[url], options])
    return res["result"]


async def _aria2_add_torrent(path: str, options: dict) -> str:
    """Add a .torrent file to aria2 (base64-encoded) and return the GID."""
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    encoded = base64.b64encode(data).decode()
    res = await _aria2_rpc("aria2.addTorrent", [encoded, [], options])
    return res["result"]


async def _aria2_tell_status(gid: str) -> dict:
    """Get the current status of an aria2 download."""
    res = await _aria2_rpc("aria2.tellStatus", [gid])
    return res.get("result", {})


async def _aria2_unpause(gid: str) -> None:
    """Unpause a paused aria2 download."""
    await _aria2_rpc("aria2.unpause", [gid])


async def _aria2_remove(gid: str) -> None:
    """Force-remove a download from aria2."""
    try:
        await _aria2_rpc("aria2.forceRemove", [gid])
    except Exception:
        pass


async def _aria2_change_option(gid: str, opts: dict) -> None:
    """Change options for an active download."""
    await _aria2_rpc("aria2.changeOption", [gid, opts])


def _aria2_name(status: dict) -> str:
    """Extract a human-readable name from aria2 status."""
    bt = status.get("bittorrent", {})
    if info := bt.get("info"):
        return info.get("name", "")
    files = status.get("files", [])
    if files:
        path = files[0].get("path", "")
        return os.path.basename(path)
    return status.get("dir", "download")


# ─────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────

async def torrent_download(
    src: str,
    dest_dir: str,
    task_id: str,
    msg,
    is_magnet: bool = False,
    existing_gid: str | None = None,
) -> list[str]:
    """
    Download a torrent (file or magnet) via aria2.
    Returns list of local file paths when complete.
    """
    os.makedirs(dest_dir, exist_ok=True)
    kb = task_kb(task_id)

    opts = {
        "dir":                       dest_dir,
        "pause":                     "false",
        "seed-time":                 "0",
        "follow-torrent":            "true",
        "max-connection-per-server": "16",
        "split":                     "16",
        "min-split-size":            "5M",
        "max-concurrent-downloads":  "5",
        "bt-enable-lpd":             "true",
        "enable-dht":                "true",
        "enable-peer-exchange":      "true",
        "bt-save-metadata":          "true",
    }

    if existing_gid:
        gid = existing_gid
        try:
            await _aria2_unpause(gid)
        except Exception:
            pass
    elif is_magnet:
        gid = await _aria2_add_uri(src, opts)
    else:
        gid = await _aria2_add_torrent(src, opts)

    tm.set_gid(task_id, gid)
    tm.set_status(task_id, "downloading")

    last_edit = 0.0
    SEP       = "━━━━━━━━━━━━━━━━━━━━━━━━"

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
            raise RuntimeError(f"Torrent error: {status.get('errorMessage', 'unknown')}")

        if st in ("complete", "removed"):
            break

        followed = status.get("followedBy", [])
        if followed:
            gid = followed[0]
            tm.set_gid(task_id, gid)
            continue

        now   = time.monotonic()
        done  = int(status.get("completedLength", 0))
        total = int(status.get("totalLength",     0))
        speed = int(status.get("downloadSpeed",   0))
        peers = int(status.get("numSeeders",       0))
        name  = _aria2_name(status) or "Downloading…"
        eta   = (total - done) / speed if speed and total > done else 0

        def _hs(b):
            for u in ("B", "KB", "MB", "GB"):
                if b < 1024: return f"{b:.1f} {u}"
                b /= 1024
            return f"{b:.1f} GB"

        pct    = int(done * 100 / total) if total else 0
        filled = int(pct / 10)
        bar    = "█" * filled + "░" * (10 - filled)
        spd_s  = _hs(speed) + "/s" if speed else "—"
        mm, ss = divmod(int(eta), 60); hh, mm2 = divmod(mm, 60)
        eta_s  = (f"{hh}h {mm2}m {ss}s" if hh else f"{mm}m {ss}s" if mm else f"{ss}s") if eta else "—"
        display = f"{name} [{peers} peers]" if peers else name
        stem    = (display[:38] + "…") if len(display) > 40 else display

        tm.update_progress(task_id, name=display, done=done, total=total,
                           speed=float(speed), eta=eta, status="downloading")

        if now - last_edit >= UPDATE_SEC:
            last_edit = now
            card = (
                f"<b>{SEP}</b>\n"
                f"<b>🧲  TORRENT</b>\n"
                f"<b>{SEP}</b>\n\n"
                f"📁 <b>{stem}</b>\n\n"
                f"<b><code>{bar}</code>  {pct}%</b>\n\n"
                f"📦 <b>{_hs(done)}</b> / <b>{_hs(total) if total else '?'}</b>\n"
                f"⚡ <b>{spd_s}</b>  👥 <b>{peers} peers</b>\n"
                f"🕐 <b>ETA: {eta_s}</b>\n\n"
                f"<b>{SEP}</b>\n"
                f"<b>⚡ {config.WATERMARK}</b>"
            )
            try:
                await msg.edit_text(card, parse_mode="html", reply_markup=kb)
            except Exception:
                pass

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
    """Get the list of files in a torrent by GID."""
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
    """Set which files to download in a multi-file torrent."""
    real_gid   = await torrent_get_real_gid(gid)
    select_str = ",".join(str(i) for i in sorted(indices)) if indices else "0"
    await _aria2_change_option(real_gid, {"select-file": select_str})


async def torrent_get_real_gid(gid: str) -> str:
    """Follow magnet metadata chain to get the real download GID."""
    try:
        status   = await _aria2_tell_status(gid)
        followed = status.get("followedBy", [])
        return followed[0] if followed else gid
    except Exception:
        return gid


async def torrent_pause(gid: str) -> None:
    """Pause an active aria2 download."""
    try:
        await _aria2_rpc("aria2.pause", [gid])
    except Exception:
        pass


async def torrent_resume(gid: str) -> None:
    """Resume a paused aria2 download."""
    await _aria2_unpause(gid)


async def torrent_remove(gid: str) -> None:
    """Remove a download from aria2."""
    await _aria2_remove(gid)
