"""
thumb_store.py — Thumbnail storage manager — NXTL
==================================================
Storage layout:
  data/thumbs/       ← user custom, permanent, 1 file per user
  data/thumb_cache/  ← auto-fetched TMDB/Fanart, 30-day TTL, 500 MB cap
  data/thumb_tmp/    ← ephemeral, wiped at startup + after upload

Resolution policy
-----------------
Pyrogram uses MTProto (not Bot API), so it accepts full-resolution thumbs.
We store and send at 1280×720 — the native HD resolution for Telegram
video/document thumbs over MTProto. The 320×320 Bot API limit does NOT apply.

Only prep_for_upload() is called right before Pyrogram send_video/send_document.
It ensures the file is:
  - JPEG
  - ≤ 200 KB   (Telegram MTProto limit)
  - 1280×720   (HD, looks great in chat)
"""

import hashlib
import os
import time

import config
from bot import LOGGER

_BASE      = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
THUMB_DIR  = os.path.join(_BASE, "thumbs")
CACHE_DIR  = os.path.join(_BASE, "thumb_cache")
TMP_DIR    = os.path.join(_BASE, "thumb_tmp")

CACHE_TTL  = 30 * 24 * 3600    # 30 days
CACHE_MAX  = 500 * 1024 * 1024  # 500 MB cap (full-res JPEGs ~50–150 KB each)

_W, _H     = 1280, 720          # HD — MTProto supports this
_MAX_BYTES = 200 * 1024         # 200 KB hard limit

for _d in (THUMB_DIR, CACHE_DIR, TMP_DIR):
    os.makedirs(_d, exist_ok=True)


# ── User custom thumb ─────────────────────────────────────────

def user_thumb_path(uid: int) -> str:
    return os.path.join(THUMB_DIR, f"{uid}.jpg")


def save_user_thumb(uid: int, src: str) -> str | None:
    """
    Save user's custom thumbnail at full 1280×720 HD.
    One file per user — overwritten on each update.
    """
    dest = user_thumb_path(uid)
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size
        # Scale to fit 1280×720 keeping aspect ratio, letterbox with black
        scale  = min(_W / w, _H / h)
        nw, nh = int(w * scale), int(h * scale)
        img    = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
        canvas.paste(img, ((_W - nw) // 2, (_H - nh) // 2))
        for q in (95, 88, 80, 70):
            canvas.save(dest, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(dest) <= _MAX_BYTES:
                break
        size_kb = os.path.getsize(dest) / 1024
        LOGGER.info(f"[ThumbStore] Saved user thumb uid={uid} ({size_kb:.0f} KB, 1280×720)")
        return dest
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] save_user_thumb failed: {e}")
        return None


def delete_user_thumb(uid: int) -> bool:
    path = user_thumb_path(uid)
    if os.path.exists(path):
        try:
            os.remove(path)
            LOGGER.info(f"[ThumbStore] Deleted user thumb uid={uid}")
            return True
        except Exception as e:
            LOGGER.warning(f"[ThumbStore] delete_user_thumb: {e}")
    return False


def get_user_thumb(uid: int) -> str | None:
    path = user_thumb_path(uid)
    return path if os.path.exists(path) and os.path.getsize(path) > 5000 else None


# ── Auto-fetch cache ──────────────────────────────────────────

def cache_key(title: str, year: str | None) -> str:
    raw = f"{title.lower().strip()}_{year or ''}".encode()
    return hashlib.md5(raw).hexdigest()


def cache_path(title: str, year: str | None) -> str:
    return os.path.join(CACHE_DIR, f"{cache_key(title, year)}.jpg")


def cache_get(title: str, year: str | None) -> str | None:
    path = cache_path(title, year)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > CACHE_TTL:
        _rm(path); return None
    if os.path.getsize(path) < 5000:
        _rm(path); return None
    LOGGER.debug(f"[ThumbStore] Cache hit: {title} ({year})")
    return path


def cache_put(title: str, year: str | None, src: str) -> str | None:
    """
    Cache at full 1280×720 HD — same resolution as what gets sent.
    Eviction keeps total under 500 MB.
    """
    dest = cache_path(title, year)
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size
        scale  = min(_W / w, _H / h)
        nw, nh = int(w * scale), int(h * scale)
        img    = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
        canvas.paste(img, ((_W - nw) // 2, (_H - nh) // 2))
        for q in (95, 88, 80, 70):
            canvas.save(dest, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(dest) <= _MAX_BYTES:
                break
        LOGGER.debug(f"[ThumbStore] Cached {title} ({year}) → {os.path.getsize(dest)//1024} KB")
        _evict_cache()
        return dest
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] cache_put failed: {e}")
        return None


def _evict_cache():
    try:
        now, files, total = time.time(), [], 0
        for fn in os.listdir(CACHE_DIR):
            if not fn.endswith(".jpg"):
                continue
            fp = os.path.join(CACHE_DIR, fn)
            mt = os.path.getmtime(fp)
            if now - mt > CACHE_TTL:
                _rm(fp); continue
            sz = os.path.getsize(fp)
            files.append((mt, sz, fp))
            total += sz
        if total > CACHE_MAX:
            files.sort(key=lambda x: x[0])
            for mt, sz, fp in files:
                _rm(fp)
                total -= sz
                if total <= CACHE_MAX * 0.8:
                    break
    except Exception as e:
        LOGGER.debug(f"[ThumbStore] evict_cache: {e}")


def cache_stats() -> dict:
    try:
        files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".jpg")]
        total = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
        return {"count": len(files), "size_mb": round(total / 1024 / 1024, 1)}
    except Exception:
        return {"count": 0, "size_mb": 0}


# ── Temp thumbs ───────────────────────────────────────────────

def tmp_path(uid: int) -> str:
    return os.path.join(TMP_DIR, f"{uid}_{int(time.time())}.jpg")


def cleanup_tmp():
    count = 0
    for fn in os.listdir(TMP_DIR):
        _rm(os.path.join(TMP_DIR, fn))
        count += 1
    if count:
        LOGGER.info(f"[ThumbStore] Cleaned {count} temp thumb(s) at startup")


# ── Prep for Telegram send ────────────────────────────────────

def prep_for_upload(src: str, dest: str | None = None) -> str | None:
    """
    Prepare the SMALL thumbnail for thumb= parameter.
    Telegram hard requirement: JPEG, ≤ 200 KB, max 320×320 px.
    This is the tiny preview shown in file lists and notification previews.
    Used for ALL send_video/send_document/send_audio thumb= calls.
    """
    if not src or not os.path.exists(src):
        return None
    out = dest or src.rsplit(".", 1)[0] + "_thumb320.jpg"
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        # thumbnail() fits within box preserving aspect ratio
        img.thumbnail((320, 320), Image.LANCZOS)
        for q in (92, 82, 72, 60):
            img.save(out, "JPEG", quality=q, optimize=True)
            if os.path.getsize(out) <= _MAX_BYTES:
                break
        return out
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] prep_for_upload (320): {e}")
        return None


def prep_cover(src: str, dest: str | None = None) -> str | None:
    """
    Prepare the HD cover for the cover= parameter in PyroTGFork send_video().

    Background:
      Telegram has TWO separate thumbnail fields in its TL schema:
        - thumb     → InputFile, max 320×320, shown in file list / notifications
        - video_cover (exposed as cover= in PyroTGFork) → full photo object,
                      accepts up to 1280×720, shown when you open/play the video

    PyroTGFork added cover= to send_video() in layer 166+.
    Sending cover= uploads the image as a full Photo (not a document thumbnail),
    which Telegram stores and displays at full resolution in the video player.

    This function outputs a letterboxed 1280×720 JPEG ≤ 200 KB.
    """
    if not src or not os.path.exists(src):
        return None
    out = dest or src.rsplit(".", 1)[0] + "_cover1280.jpg"
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size
        scale  = min(_W / w, _H / h)
        nw, nh = int(w * scale), int(h * scale)
        img    = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
        canvas.paste(img, ((_W - nw) // 2, (_H - nh) // 2))
        for q in (95, 88, 80, 70):
            canvas.save(out, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(out) <= _MAX_BYTES:
                break
        size_kb = os.path.getsize(out) / 1024
        LOGGER.debug(f"[ThumbStore] cover prep: {nw}×{nh} → {size_kb:.0f} KB")
        return out
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] prep_cover (1280): {e}")
        return None


# ── Helper ────────────────────────────────────────────────────

def _rm(path: str):
    try: os.remove(path)
    except Exception: pass
