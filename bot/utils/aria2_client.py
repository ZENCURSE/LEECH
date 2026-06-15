"""
aria2_client.py — NXTL stub
Torrent/magnet now handled by qBittorrent.
These no-op stubs exist only for backward compatibility with any remaining imports.
"""
async def _noop(*a, **k): return None

# Backward-compat stubs (no-ops)
_aria2_add_uri     = _noop  # noqa: stub
_aria2_add_torrent = _noop  # noqa: stub
_aria2_tell_status = _noop  # noqa: stub
_aria2_name        = _noop  # noqa: stub
_aria2_rpc         = _noop  # noqa: stub

def _empty_list(*a, **k): return []

torrent_get_files    = _empty_list   # noqa: stub
torrent_set_selected = _noop         # noqa: stub
torrent_resume       = _noop         # noqa: stub
torrent_remove       = _noop         # noqa: stub
torrent_pause        = _noop         # noqa: stub
torrent_download     = _noop         # noqa: stub
