"""
aria2_client.py — thin JSON-RPC client for the aria2c daemon started in
main.py (_start_aria2). Used for BOTH multi-connection HTTP downloads and
torrent/magnet downloads — one daemon, one client, no more qBittorrent
dependency for torrents and no more single-connection aiohttp streaming
for HTTP.
"""

import base64

import aiohttp

import config

_RPC_URL = f"{config.ARIA2_HOST}:{config.ARIA2_PORT}/jsonrpc"
_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)


async def _call(method: str, params: list | None = None):
    params = list(params or [])
    secret = getattr(config, "ARIA2_SECRET", "").strip()
    if secret:
        params = [f"token:{secret}"] + params
    payload = {"jsonrpc": "2.0", "id": "nxtl", "method": method, "params": params}
    async with aiohttp.ClientSession() as session:
        async with session.post(_RPC_URL, json=payload, timeout=_TIMEOUT) as r:
            data = await r.json(content_type=None)
    if "error" in data:
        raise RuntimeError(data["error"].get("message", f"aria2 RPC error: {method}"))
    return data.get("result")


async def add_uri(uris: list[str], dest_dir: str, options: dict | None = None) -> str:
    """Add an HTTP(S)/magnet URI. Returns the aria2 gid."""
    opts = {"dir": dest_dir}
    if options:
        opts.update(options)
    return await _call("aria2.addUri", [uris, opts])


async def add_torrent(torrent_path: str, dest_dir: str, options: dict | None = None) -> str:
    """Add a .torrent file (read from disk, base64-encoded per aria2 RPC spec)."""
    with open(torrent_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    opts = {"dir": dest_dir}
    if options:
        opts.update(options)
    return await _call("aria2.addTorrent", [b64, [], opts])


async def tell_status(gid: str, keys: list[str] | None = None) -> dict:
    params = [gid] if keys is None else [gid, keys]
    return await _call("aria2.tellStatus", params)


async def get_files(gid: str) -> list:
    return await _call("aria2.getFiles", [gid])


async def remove(gid: str) -> None:
    for method in ("aria2.forceRemove", "aria2.remove"):
        try:
            await _call(method, [gid])
            return
        except Exception:
            continue


async def pause(gid: str) -> None:
    try:
        await _call("aria2.forcePause", [gid])
    except Exception:
        pass


async def unpause(gid: str) -> None:
    try:
        await _call("aria2.unpause", [gid])
    except Exception:
        pass
