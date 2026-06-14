"""
qBittorrent Downloader — NXTL
Torrents and magnets via qBittorrent Web API.
Supports web-based file selection for multi-file torrents.
Config: QBT_HOST, QBT_PORT, QBT_USERNAME, QBT_PASSWORD, BASE_URL
"""
import os
import asyncio
import time
import hashlib
from secrets import token_hex

import config
from bot.core import task_manager as tm
from bot.utils.progress import task_kb, downloading_card, queued_card, processing_card
from bot.utils.size_utils import human_size
from web.mega_selection_store import write_state, read_state, delete_state

UPDATE_SEC    = 5
POLL_INTERVAL = 3
STALL_TIMEOUT = 600        # 10 min stall → error
MAX_WAIT_SECS = 7200       # 2 h total cap

# In-memory selection state: gid → {event, qbt_client, hash, dest, task_id, msg, uid}
_qbt_selections: dict[str, dict] = {}


# ── qBittorrent client factory ────────────────────────────────
def _client():
    import qbittorrentapi
    return qbittorrentapi.Client(
        host     = getattr(config, "QBT_HOST",     "localhost"),
        port     = getattr(config, "QBT_PORT",     8090),
        username = getattr(config, "QBT_USERNAME", "admin"),
        password = getattr(config, "QBT_PASSWORD", "adminadmin"),
        REQUESTS_ARGS={"timeout": 15},
        VERIFY_WEBUI_CERTIFICATE=False,
    )


def _state_label(state: str) -> str:
    return {
        "downloading": "Downloading", "stalledDL":  "Stalled",
        "queuedDL":    "Queued",      "checkingDL": "Checking",
        "metaDL":      "Fetching metadata",
        "forcedDL":    "Downloading", "uploading":  "Seeding",
        "stalledUP":   "Seeding",     "queuedUP":   "Seeding",
        "pausedDL":    "Paused",      "error":      "Error",
    }.get(state, state.title())


# ══════════════════════════════════════════════════════════════
#  File list builder (for web picker)
# ══════════════════════════════════════════════════════════════
def _build_file_list(qbt, info_hash: str) -> list[dict]:
    """Return all files in a torrent as a list of dicts for the web picker."""
    try:
        files = qbt.torrents_files(torrent_hash=info_hash)
        result = []
        for f in files:
            parts = f.name.rsplit("/", 1)
            result.append({
                "id":   str(f.index),
                "name": parts[-1],
                "path": "/" + parts[0] + "/" if len(parts) > 1 else "/",
                "size": f.size,
            })
        return result
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════
#  Main downloader
# ══════════════════════════════════════════════════════════════
async def qbt_download(
    source: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
    select_files: bool = False,
) -> str:
    loop = asyncio.get_event_loop()
    kb   = task_kb(task_id)
    os.makedirs(dest_dir, exist_ok=True)

    # ── Connect ───────────────────────────────────────────────
    try:
        qbt = await loop.run_in_executor(None, _client)
        await loop.run_in_executor(None, qbt.auth_log_in)
    except Exception as e:
        raise RuntimeError(f"qBittorrent: cannot connect — {e}") from e

    # ── Add torrent/magnet ────────────────────────────────────
    save_path = os.path.abspath(dest_dir)
    gid       = token_hex(6)

    def _add():
        if source.startswith("magnet:") or source.startswith("http"):
            qbt.torrents_add(
                urls=source, save_path=save_path,
                category="nxtl", use_auto_torrent_management=False,
                stopped=select_files,        # pause until user picks files
            )
        else:
            with open(source, "rb") as f:
                raw = f.read()
            qbt.torrents_add(
                torrent_files=raw, save_path=save_path,
                category="nxtl", use_auto_torrent_management=False,
                stopped=select_files,
            )

    try:
        await loop.run_in_executor(None, _add)
        await asyncio.sleep(3)   # let qBittorrent index it
    except Exception as e:
        raise RuntimeError(f"qBittorrent: failed to add — {e}") from e

    # Discover hash
    def _find_hash():
        torrents = qbt.torrents_info(category="nxtl")
        return torrents[-1].hash if torrents else None

    info_hash = await loop.run_in_executor(None, _find_hash)
    if not info_hash:
        raise RuntimeError("qBittorrent: could not find added torrent.")

    tm.set_status(task_id, "queued")

    # ── Optional web file picker ──────────────────────────────
    if select_files:
        base_url = getattr(config, "BASE_URL", "").rstrip("/")
        if not base_url:
            # No web server — just resume everything
            await loop.run_in_executor(None, lambda: qbt.torrents_resume(torrent_hashes=info_hash))
        else:
            file_list = await loop.run_in_executor(None, _build_file_list, qbt, info_hash)
            if file_list:
                write_state(gid, file_list, [str(f["id"]) for f in file_list])  # all selected by default
                _qbt_selections[gid] = {
                    "event":     asyncio.Event(),
                    "qbt":       qbt,
                    "hash":      info_hash,
                    "dest":      dest_dir,
                    "task_id":   task_id,
                    "msg":       msg,
                    "uid":       uid,
                    "loop":      loop,
                }
                sel_url = f"{base_url}/qbt-select/{gid}"
                from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                kb_sel = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📂 Select Files", url=sel_url)
                ]])
                try:
                    await msg.edit_text(
                        f"🧲 <b>Torrent Ready — Choose Files</b>\n\n"
                        f"Open the file picker to choose which files to download.\n"
                        f"All files are pre-selected — deselect any you don't need.\n\n"
                        f"<i>Session expires in 30 minutes.</i>",
                        reply_markup=kb_sel,
                        parse_mode="html",
                    )
                except Exception:
                    pass

                # Wait for user to confirm (or timeout at 30 min)
                try:
                    await asyncio.wait_for(_qbt_selections[gid]["event"].wait(), timeout=1800)
                except asyncio.TimeoutError:
                    _qbt_selections.pop(gid, None)
                    delete_state(gid)
                    await loop.run_in_executor(
                        None, lambda: qbt.torrents_delete(delete_files=True, torrent_hashes=info_hash)
                    )
                    raise RuntimeError("Torrent file selection timed out (30 min). Send the link again.")

                if tm.is_cancelled(task_id):
                    raise asyncio.CancelledError

                # Apply file priority: deselected files → skip (priority 0)
                state = read_state(gid)
                delete_state(gid)
                selected_ids = set(state.get("selected_ids", []) if state else [])
                if selected_ids:
                    all_files = await loop.run_in_executor(None, _build_file_list, qbt, info_hash)

                    def _set_priorities():
                        for f in all_files:
                            prio = 1 if f["id"] in selected_ids else 0
                            qbt.torrents_file_priority(
                                torrent_hash=info_hash,
                                file_id=int(f["id"]),
                                priority=prio,
                            )
                    await loop.run_in_executor(None, _set_priorities)
            # Resume torrent
            await loop.run_in_executor(None, lambda: qbt.torrents_resume(torrent_hashes=info_hash))

    # ── Poll progress ─────────────────────────────────────────
    last_upd      = 0.0
    last_progress = 0.0
    stall_since   = time.time()
    started       = time.time()
    torrent_name  = source[:40]

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        if tm.is_cancelled(task_id):
            await loop.run_in_executor(
                None, lambda: qbt.torrents_delete(delete_files=False, torrent_hashes=info_hash)
            )
            raise asyncio.CancelledError

        if time.time() - started > MAX_WAIT_SECS:
            raise RuntimeError("qBittorrent: exceeded 2-hour time limit.")

        try:
            torrents = await loop.run_in_executor(
                None, lambda: qbt.torrents_info(torrent_hashes=info_hash)
            )
        except Exception:
            await asyncio.sleep(5)
            continue

        if not torrents:
            await asyncio.sleep(5)
            continue

        t         = torrents[0]
        state     = t.state
        progress  = t.progress
        done      = t.downloaded
        total     = t.size
        speed     = t.dlspeed
        eta_raw   = t.eta
        eta       = float(eta_raw) if eta_raw < 8640000 else 0.0
        torrent_name = t.name or torrent_name

        # Stall detection
        if progress > last_progress:
            last_progress = progress
            stall_since   = time.time()
        elif state in ("stalledDL", "pausedDL") and time.time() - stall_since > STALL_TIMEOUT:
            raise RuntimeError(f"qBittorrent: no progress for {STALL_TIMEOUT//60} min — check seeds.")

        if state in ("error", "missingFiles"):
            raise RuntimeError(f"qBittorrent: {state}")

        tm.update_progress(
            task_id, name=torrent_name,
            done=done, total=total, speed=speed, eta=eta,
            status="downloading",
        )

        now = time.monotonic()
        if now - last_upd >= UPDATE_SEC:
            last_upd = now
            try:
                await msg.edit_text(
                    downloading_card(torrent_name, done, total, speed, eta, task_id, started),
                    reply_markup=kb, parse_mode="html",
                )
            except Exception:
                pass

        if state in ("uploading", "stalledUP", "queuedUP") or progress >= 1.0:
            break

    # ── Stop seeding ──────────────────────────────────────────
    try:
        await loop.run_in_executor(None, lambda: qbt.torrents_stop(torrent_hashes=info_hash))
    except Exception:
        pass

    # ── Find output ───────────────────────────────────────────
    content_path = os.path.join(dest_dir, torrent_name)
    if not os.path.exists(content_path):
        items = [
            os.path.join(dest_dir, x) for x in os.listdir(dest_dir)
            if not x.endswith((".part", ".!qB", ".tmp"))
        ]
        content_path = max(items, key=os.path.getmtime) if items else content_path

    if not os.path.exists(content_path):
        raise FileNotFoundError(f"qBittorrent: output not found at {content_path}")

    return content_path


# ══════════════════════════════════════════════════════════════
#  Called by web/app.py when user confirms torrent selection
# ══════════════════════════════════════════════════════════════
async def resume_qbt_with_selection(gid: str):
    """Signal the waiting qbt_download to continue after web selection."""
    state = _qbt_selections.get(gid)
    if state:
        state["event"].set()
