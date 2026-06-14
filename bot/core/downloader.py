"""
Download core — NXTL (Refactored)

This module now acts as a unified entry point that re-exports from the
separate downloader modules in bot/downloaders/.

Each protocol has its own file for easy maintenance:
  - http_downloader.py    — HTTP/HTTPS streaming
  - ytdlp_downloader.py  — yt-dlp (YouTube, M3U8, 1000+ sites)
  - aria2_downloader.py  — Aria2 JSON-RPC (torrent/magnet)
  - mega_downloader.py   — Mega.nz
  - jd_downloader.py     — Multi-host direct link (JDLeech)
  - telegram_downloader.py — Telegram media
  - direct_link_generator.py — URL resolver (NEO-WZML + NXTL)
"""

from bot.downloaders.http_downloader import http_download
from bot.downloaders.ytdlp_downloader import ytdlp_download
from bot.downloaders.aria2_downloader import (
    torrent_download,
    torrent_get_files,
    torrent_set_selected,
    torrent_get_real_gid,
    torrent_pause,
    torrent_resume,
    torrent_remove,
)
from bot.downloaders.mega_downloader import mega_download
from bot.downloaders.jd_downloader import jdleech_download
from bot.downloaders.telegram_downloader import telegram_download
from bot.downloaders.direct_link_generator import generate_direct_link

__all__ = [
    "http_download",
    "ytdlp_download",
    "torrent_download",
    "torrent_get_files",
    "torrent_set_selected",
    "torrent_get_real_gid",
    "torrent_pause",
    "torrent_resume",
    "torrent_remove",
    "mega_download",
    "jdleech_download",
    "telegram_download",
    "generate_direct_link",
]
