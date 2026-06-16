"""
thumb_store.py — Thumbnail storage manager — NXTL
==================================================
Handles all thumbnail persistence:

  1. User custom thumbs  (data/thumbs/<uid>.jpg)
     - One file per user, overwritten on update
     - MongoDB stores only the path string (no binary)
     - File deleted when user clears thumb

  2. Auto-fetch cache  (data/thumb_cache/<md5>.jpg)
     - Keyed by md5(title+year) so same film reuses cached result
     - TTL: 30 days, auto-evicted by _evict_cache()
     - Max size: 200 MB — oldest files pruned when exceeded

  3. Temp thumbs  (data/thumb_tmp/<uid>_<ts>.jpg)
     - Used during upload, deleted immediately after send

Storage layout (all under /app/data or DOWNLOAD_DIR/data):
  data/
    thumbs/           ← user custom, permanent per-user
    thumb_cache/      ← auto-fetch cache, 30-day TTL, 200 MB cap
    thumb_tmp/        ← ephemeral, cleaned at startup + after use
"""

import hashlib
import os
import time

import config
from bot import LOGGER

_BASE       = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
THUMB_DIR   = os.path.join(_BASE, "thumbs")        # user custom
CACHE_DIR   = os.path.join(_BASE, "thumb_cache")   # auto-fetch cache
TMP_DIR     = os.path.join(_BASE, "thumb_tmp")     # ephemeral

CACHE_TTL   = 30 * 24 * 3600   # 30 days
CACHE_MAX   = 200 * 1024 * 1024  # 200 MB cap

for _d in (THUMB_DIR, CACHE_DIR, TMP_DIR):
    os.makedirs(_d, exist_ok=True)


# ── User custom thumb ─────────────────────────────────────────

def user_thumb_path(uid: int) -> str:
    """Permanent path for a user's custom thumbnail."""
    return os.path.join(THUMB_DIR, f"{uid}.jpg")


def save_user_thumb(uid: int, src: str) -> str | None:
    """
    Save user's custom thumbnail.
    Converts to 320×320 JPEG for storage (small, universal compat).
    Returns saved path or None on failure.
    """
    dest = user_thumb_path(uid)
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        img.thumbnail((320, 320), Image.LANCZOS)
        img.save(dest, "JPEG", quality=90, optimize=True)
        size_kb = os.path.getsize(dest) / 1024
        LOGGER.info(f"[ThumbStore] Saved user thumb uid={uid} ({size_kb:.0f} KB) → {dest}")
        return dest
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] save_user_thumb failed: {e}")
        return None


def delete_user_thumb(uid: int) -> bool:
    """Delete a user's stored thumbnail."""
    path = user_thumb_path(uid)
    if os.path.exists(path):
        try:
            os.remove(path)
            LOGGER.info(f"[ThumbStore] Deleted user thumb uid={uid}")
            return True
        except Exception as e:
            LOGGER.warning(f"[ThumbStore] delete_user_thumb failed: {e}")
    return False


def get_user_thumb(uid: int) -> str | None:
    """Return user's custom thumb path if it exists."""
    path = user_thumb_path(uid)
    return path if os.path.exists(path) and os.path.getsize(path) > 1000 else None


# ── Auto-fetch cache ──────────────────────────────────────────

def cache_key(title: str, year: str | None) -> str:
    raw = f"{title.lower().strip()}_{year or ''}".encode()
    return hashlib.md5(raw).hexdigest()


def cache_path(title: str, year: str | None) -> str:
    return os.path.join(CACHE_DIR, f"{cache_key(title, year)}.jpg")


def cache_get(title: str, year: str | None) -> str | None:
    """Return cached thumbnail path if valid and not expired."""
    path = cache_path(title, year)
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > CACHE_TTL:
        _rm(path)
        return None
    if os.path.getsize(path) < 5000:
        _rm(path)
        return None
    return path


def cache_put(title: str, year: str | None, src: str) -> str | None:
    """
    Save a thumbnail to the auto-fetch cache.
    Stores as 320×320 JPEG — small enough for Telegram, fast to serve.
    Runs eviction after saving.
    """
    dest = cache_path(title, year)
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        # Keep aspect ratio, fit within 320×320
        img.thumbnail((320, 320), Image.LANCZOS)
        img.save(dest, "JPEG", quality=88, optimize=True)
        LOGGER.debug(f"[ThumbStore] Cached: {title} ({year}) → {os.path.getsize(dest)//1024} KB")
        _evict_cache()
        return dest
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] cache_put failed: {e}")
        return None


def _evict_cache():
    """
    Remove expired files and enforce 200 MB cap.
    Called after every cache write — lightweight since most runs do nothing.
    """
    try:
        now   = time.time()
        files = []
        total = 0
        for fn in os.listdir(CACHE_DIR):
            if not fn.endswith(".jpg"):
                continue
            fp   = os.path.join(CACHE_DIR, fn)
            mtime = os.path.getmtime(fp)
            size  = os.path.getsize(fp)

            # Remove expired
            if now - mtime > CACHE_TTL:
                _rm(fp)
                continue

            files.append((mtime, size, fp))
            total += size

        # Enforce size cap — remove oldest first
        if total > CACHE_MAX:
            files.sort(key=lambda x: x[0])   # oldest first
            for mtime, size, fp in files:
                _rm(fp)
                total -= size
                if total <= CACHE_MAX * 0.8:  # prune to 80% of cap
                    break
    except Exception as e:
        LOGGER.debug(f"[ThumbStore] evict_cache error: {e}")


def cache_stats() -> dict:
    """Return cache stats for /status command."""
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
    """Delete all temp thumbs — called at bot startup."""
    count = 0
    for fn in os.listdir(TMP_DIR):
        fp = os.path.join(TMP_DIR, fn)
        _rm(fp)
        count += 1
    if count:
        LOGGER.info(f"[ThumbStore] Cleaned {count} temp thumb(s) at startup")


# ── Prep for Telegram upload ──────────────────────────────────

def prep_for_upload(src: str, dest: str | None = None) -> str | None:
    """
    Convert any image → 320×320 JPEG ≤ 200 KB for Telegram.
    320×320 works with both Bot API and MTProto.
    """
    if not src or not os.path.exists(src):
        return None
    out = dest or tmp_path(0)
    try:
        from PIL import Image
        img = Image.open(src).convert("RGB")
        img.thumbnail((320, 320), Image.LANCZOS)
        for q in (90, 80, 70, 60):
            img.save(out, "JPEG", quality=q, optimize=True)
            if os.path.getsize(out) <= 200 * 1024:
                break
        return out if os.path.exists(out) else None
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] prep_for_upload failed: {e}")
        return None


# ── Helper ────────────────────────────────────────────────────

def _rm(path: str):
    try:
        os.remove(path)
    except Exception:
        pass
