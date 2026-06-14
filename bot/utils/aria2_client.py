"""
Thin shim over downloader.py's JSON-RPC functions.
Kept for backward compatibility.
"""
import base64
import aiohttp
import aiofiles
import config

from bot.core.downloader import (
    _aria2_add_uri,
    _aria2_add_torrent,
    _aria2_tell_status,
    _aria2_name,
    _aria2_rpc,
    torrent_remove as remove,
    torrent_resume as resume,
    torrent_set_selected as set_selected_files,
    torrent_get_files as get_files,
)


async def add_magnet(magnet: str, dest_dir: str) -> object:
    opts = {"dir": dest_dir, "pause": "true", "seed-time": "0"}
    gid  = await _aria2_add_uri(magnet, opts)
    return type("DL", (), {"gid": gid, "files": []})()


async def add_torrent(torrent_path: str, dest_dir: str) -> object:
    opts = {"dir": dest_dir, "pause": "true", "seed-time": "0"}
    gid  = await _aria2_add_torrent(torrent_path, opts)
    return type("DL", (), {"gid": gid, "files": []})()


async def get_download(gid: str) -> object:
    status = await _aria2_tell_status(gid)
    dl = type("DL", (), {})()
    dl.gid              = gid
    dl.status           = status.get("status", "unknown")
    dl.completed_length = int(status.get("completedLength", 0))
    dl.total_length     = int(status.get("totalLength", 0))
    dl.download_speed   = int(status.get("downloadSpeed", 0))
    dl.name             = _aria2_name(status)
    dl.error_message    = status.get("errorMessage", "")
    dl.files            = [
        type("F", (), {
            "index":  int(f.get("index", i + 1)),
            "path":   f.get("path", ""),
            "length": int(f.get("length", 0)),
        })()
        for i, f in enumerate(status.get("files", []))
    ]
    return dl
