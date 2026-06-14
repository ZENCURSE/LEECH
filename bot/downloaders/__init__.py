# NXTL Downloaders Package
# Each file handles a specific download protocol/service
# Import all downloaders here for easy access

from .aria2_downloader import torrent_download, torrent_get_files, torrent_set_selected, torrent_pause, torrent_resume, torrent_remove
from .ytdlp_downloader import ytdlp_download
from .http_downloader import http_download
from .mega_downloader import mega_download
from .jd_downloader import jdleech_download
from .telegram_downloader import telegram_download
from .direct_link_generator import generate_direct_link

__all__ = [
    "torrent_download",
    "torrent_get_files",
    "torrent_set_selected",
    "torrent_pause",
    "torrent_resume",
    "torrent_remove",
    "ytdlp_download",
    "http_download",
    "mega_download",
    "jdleech_download",
    "telegram_download",
    "generate_direct_link",
]
