"""
qBittorrent Downloader — NXTL
Downloads torrents and magnets via qBittorrent Web API.
Requires qBittorrent with Web UI enabled.
Config: QBT_HOST, QBT_PORT, QBT_USERNAME, QBT_PASSWORD in config.py
"""
import os
import asyncio
import time
import hashlib
import config

from bot.core import task_manager as tm
from bot.utils.progress import task_kb, downloading_card, queued_card
from bot.utils.size_utils import human_size

UPDATE_SEC    = 5
POLL_INTERVAL = 3          # seconds between status polls
STALL_TIMEOUT = 600        # 10 min with no progress → error
MAX_WAIT_SECS = 7200       # 2-hour total cap per torrent


# ── Client ────────────────────────────────────────────────────
def _qbt_client():
    import qbittorrentapi
    return qbittorrentapi.Client(
        host     = getattr(config, "QBT_HOST",     "localhost"),
        port     = getattr(config, "QBT_PORT",     8080),
        username = getattr(config, "QBT_USERNAME", "admin"),
        password = getattr(config, "QBT_PASSWORD", "adminadmin"),
        REQUESTS_ARGS={"timeout": 15},
        VERIFY_WEBUI_CERTIFICATE=False,
    )


def _torrent_hash(torrent_path_or_magnet: str) -> str | None:
    """Extract info-hash from magnet or compute it from .torrent bytes."""
    if torrent_path_or_magnet.startswith("magnet:"):
        import re
        m = re.search(r"urn:btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", torrent_path_or_magnet)
        if m:
            raw = m.group(1)
            if len(raw) == 32:          # base32
                import base64
                raw = base64.b32decode(raw + "=" * ((8 - len(raw) % 8) % 8)).hex()
            return raw.lower()
    return None


def _state_label(state: str) -> str:
    mapping = {
        "downloading":        "Downloading",
        "stalledDL":          "Stalled",
        "queuedDL":           "Queued",
        "checkingDL":         "Checking",
        "checkingResumeData": "Checking",
        "metaDL":             "Fetching metadata",
        "forcedDL":           "Downloading",
        "uploading":          "Seeding",
        "stalledUP":          "Seeding",
        "queuedUP":           "Seeding",
        "pausedDL":           "Paused",
        "error":              "Error",
        "missingFiles":       "Missing files",
        "moving":             "Moving",
    }
    return mapping.get(state, state.title())


# ══════════════════════════════════════════════════════════════
#  Main downloader
# ══════════════════════════════════════════════════════════════
async def qbt_download(
    source: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    """
    Download a torrent or magnet via qBittorrent.
    source  — magnet link, .torrent URL, or path to .torrent file
    Returns the path to the downloaded content.
    """
    loop    = asyncio.get_running_loop()
    kb      = task_kb(task_id)
    os.makedirs(dest_dir, exist_ok=True)

    def _add_torrent(qbt):
        """Add torrent to qBittorrent, return the hash."""
        qbt.auth_log_in()
        save_path = os.path.abspath(dest_dir)

        if source.startswith("magnet:") or source.startswith("http"):
            qbt.torrents_add(
                urls=source,
                save_path=save_path,
                category="nxtl",
                use_auto_torrent_management=False,
            )
            info_hash = _torrent_hash(source)
            if not info_hash:
                # Wait briefly then find it by most-recent
                time.sleep(3)
                torrents = qbt.torrents_info(category="nxtl")
                if not torrents:
                    raise RuntimeError("Torrent not found after adding.")
                info_hash = torrents[-1].hash
        else:
            # Local .torrent file
            with open(source, "rb") as f:
                raw = f.read()
            qbt.torrents_add(
                torrent_files=raw,
                save_path=save_path,
                category="nxtl",
                use_auto_torrent_management=False,
            )
            # Hash from torrent file
            import torrent_parser as tp
            meta = tp.parse(source)
            info = meta[b"info"]
            import bencodepy
            info_hash = hashlib.sha1(bencodepy.encode(info)).hexdigest()

        return info_hash

    # ── Add torrent in executor ───────────────────────────────
    try:
        qbt = await loop.run_in_executor(None, _qbt_client)
    except Exception as e:
        raise RuntimeError(f"qBittorrent: cannot connect — {e}") from e

    try:
        info_hash = await loop.run_in_executor(None, _add_torrent, qbt)
    except Exception as e:
        raise RuntimeError(f"qBittorrent: failed to add torrent — {e}") from e

    tm.set_status(task_id, "downloading")

    # ── Edit a "queued" card immediately ──────────────────────
    try:
        await msg.edit_text(
            queued_card(source[:40] + "…" if len(source) > 40 else source, task_id),
            reply_markup=kb,
            parse_mode="html",
        )
    except Exception:
        pass

    # ── Poll loop ─────────────────────────────────────────────
    last_upd      = 0.0
    last_progress = 0
    stall_since   = time.time()
    started       = time.time()

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        if tm.is_cancelled(task_id):
            # Remove from qBittorrent
            try:
                await loop.run_in_executor(
                    None,
                    lambda: qbt.torrents_delete(delete_files=False, torrent_hashes=info_hash)
                )
            except Exception:
                pass
            raise asyncio.CancelledError

        # Timeout guard
        if time.time() - started > MAX_WAIT_SECS:
            raise RuntimeError("qBittorrent: download exceeded max time limit (2 h).")

        # Fetch status
        try:
            torrents = await loop.run_in_executor(
                None,
                lambda: qbt.torrents_info(torrent_hashes=info_hash)
            )
        except Exception:
            await asyncio.sleep(5)
            continue

        if not torrents:
            await asyncio.sleep(5)
            continue

        t = torrents[0]

        state    = t.state
        progress = t.progress          # 0.0–1.0
        done     = t.downloaded        # bytes
        total    = t.size              # bytes
        speed    = t.dlspeed           # bytes/s
        eta_raw  = t.eta               # seconds (8640000 = no eta)
        eta      = float(eta_raw) if eta_raw < 8640000 else 0.0
        name     = t.name or source[:40]

        # Stall detection
        if progress > last_progress:
            last_progress = progress
            stall_since   = time.time()
        elif state in ("stalledDL", "pausedDL") and time.time() - stall_since > STALL_TIMEOUT:
            raise RuntimeError(f"qBittorrent: stalled for >{STALL_TIMEOUT//60} min. Check seeds.")

        # Error state
        if state in ("error", "missingFiles"):
            raise RuntimeError(f"qBittorrent error: {state} — {t.state_enum}")

        # Update task manager
        pct = int(progress * 100)
        tm.update_progress(
            task_id, name=name,
            done=done, total=total,
            speed=speed, eta=eta,
            status="downloading",
        )

        # Update card
        now = time.monotonic()
        if now - last_upd >= UPDATE_SEC:
            last_upd = now
            try:
                await msg.edit_text(
                    downloading_card(name, done, total, speed, eta, task_id, started),
                    reply_markup=kb,
                    parse_mode="html",
                )
            except Exception:
                pass

        # Done?
        if state in ("uploading", "stalledUP", "queuedUP") or pct >= 100:
            break

    # ── Find downloaded content ───────────────────────────────
    content_path = os.path.join(dest_dir, t.name)
    if not os.path.exists(content_path):
        # Fall back to most recently modified item in dest_dir
        items = [
            os.path.join(dest_dir, x)
            for x in os.listdir(dest_dir)
            if not x.endswith((".part", ".!qB", ".tmp"))
        ]
        if items:
            content_path = max(items, key=os.path.getmtime)

    if not os.path.exists(content_path):
        raise FileNotFoundError(f"qBittorrent: content not found at {content_path}")

    # ── Optional: stop seeding ────────────────────────────────
    try:
        await loop.run_in_executor(
            None,
            lambda: qbt.torrents_stop(torrent_hashes=info_hash)
        )
    except Exception:
        pass

    return content_path
