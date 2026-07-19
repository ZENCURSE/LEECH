# NXTL Downloaders Package

from .ytdlp_downloader       import ytdlp_download
from .aria2_downloader       import http_download, torrent_download as qbt_download
from .jd_downloader          import jd_download
from .telegram_downloader    import telegram_download
from .direct_link_generator  import generate_direct_link
from .gdrive_downloader      import gdrive_download

__all__ = [
    "ytdlp_download",
    "http_download",
    "qbt_download",
    "jd_download",
    "telegram_download",
    "generate_direct_link",
    "gdrive_download",
]
