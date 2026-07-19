"""
bot/core/downloader.py — unified re-export hub for all download backends.
HTTP and torrent/magnet both go through aria2c (multi-connection).
"""

from bot.downloaders.aria2_downloader    import http_download, torrent_download as qbt_download
from bot.downloaders.ytdlp_downloader   import ytdlp_download
from bot.downloaders.jd_downloader      import jd_download
from bot.downloaders.telegram_downloader import telegram_download
from bot.utils.direct_link_generator import generate_direct_link, is_supported

__all__ = [
    "http_download",
    "ytdlp_download",
    "jd_download",
    "qbt_download",
    "telegram_download",
    "generate_direct_link",
    "is_supported",
]
