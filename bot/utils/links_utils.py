# Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML)

from re import match as re_match
from base64 import urlsafe_b64decode, urlsafe_b64encode

_URL_MAX_LEN = 8192
_URL_RE = (
    r"^(?!/)"
    r"(rtmps?://|mms://|rtsp://|https?://|ftp://)?"
    r"([^/:\s]+:[^/@\s]+@)?"
    r"(www\.)?"
    r"([^/:\s]+\.[^/:\s]+)"
    r"(:\d+)?"
    r"(/\S*)?"
    r"$"
)


def is_magnet(url: str) -> bool:
    return bool(re_match(
        r"^magnet:\?.*xt=urn:(btih|btmh):([a-zA-Z0-9]{32,40}|[a-z2-7]{32}).*", url
    ))


def is_url(url: str) -> bool:
    if not isinstance(url, str) or not url or len(url) > _URL_MAX_LEN:
        return False
    return bool(re_match(_URL_RE, url))


def is_gdrive_link(url: str) -> bool:
    return "drive.google.com" in url or "drive.usercontent.google.com" in url


def is_telegram_link(url: str) -> bool:
    return url.startswith(("https://t.me/", "tg://openmessage?user_id="))


def is_mega_link(url: str) -> bool:
    return "mega.nz" in url or "mega.co.nz" in url


def get_mega_link_type(url: str) -> str:
    return "folder" if "folder" in url or "/#F!" in url else "file"


def is_share_link(url: str) -> bool:
    return bool(re_match(
        r"https?:\/\/.+\.gdtot\.\S+|https?:\/\/(filepress|filebee|appdrive|gdflix)\.\S+",
        url,
    ))


def is_rclone_path(path: str) -> bool:
    return bool(re_match(
        r"^(mrcc:)?(?!(magnet:|mtp:|sa:|tp:))(?![- ])[a-zA-Z0-9_\. -]+(?<! ):(?!.*\/\/).*$|^rcl$",
        path,
    ))


def encode_slink(string: str) -> str:
    return (urlsafe_b64encode(string.encode("ascii")).decode("ascii")).strip("=")


def decode_slink(b64_str: str) -> str:
    return urlsafe_b64decode(
        (b64_str.strip("=") + "=" * (-len(b64_str.strip("=")) % 4)).encode("ascii")
    ).decode("ascii")
