"""
aria2_client.py — NXTL
aria2 is still used for HTTP downloads via aria2c daemon.
Torrent/magnet downloads now handled by qBittorrent.
"""
# Stubs for backward compatibility — these functions are no longer needed
async def dummy(*a, **kw): return None

_aria2_add_uri        = dummy
_aria2_add_torrent    = dummy
_aria2_tell_status    = dummy
_aria2_name           = dummy
_aria2_rpc            = dummy
torrent_get_files     = dummy
torrent_set_selected  = dummy
torrent_resume        = dummy
torrent_remove        = dummy
