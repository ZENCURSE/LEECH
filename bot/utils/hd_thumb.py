"""
hd_thumb.py — Compatibility shim
All logic lives in thumbnail.py (unified system).
This module re-exports the functions that the rest of the codebase imports.
"""
from bot.utils.thumbnail import (
    prep_thumb,
    generate_hd_thumb,
    generate_title_card,
    get_thumbnail,
)

# _hq_resize_thumb used by uploader.py and media_utils.py
def _hq_resize_thumb(src: str, dest: str,
                     max_w: int = 1280, max_h: int = 720) -> str | None:
    return prep_thumb(src, dest)


__all__ = [
    "prep_thumb",
    "generate_hd_thumb",
    "generate_title_card",
    "get_thumbnail",
    "_hq_resize_thumb",
]
