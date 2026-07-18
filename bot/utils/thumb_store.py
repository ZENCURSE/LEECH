"""
thumb_store.py — Thumbnail storage manager — NXTL
==================================================
Storage layout:
  data/thumbs/       ← user custom, permanent, 1 file per user
  data/thumb_cache/  ← auto-fetched TMDB/Fanart, 30-day TTL, 500 MB cap
  data/thumb_tmp/    ← ephemeral, wiped at startup + after upload

Resolution policy
-----------------
TWO separate thumbnail fields in Telegram TL schema:

  thumb=  (InputFile) — small preview shown in file list / notifications
          Max 320×320 px, ≤ 200 KB. Hard Telegram limit.

  cover=  (InputFile Photo) — HD image shown when video is opened/played.
          PyroTGFork send_video(cover=) / aiogram send_video(cover=)
          Accepts JPEG up to 1280×720, up to 5 MB.
          aiogram>=3.18.0 properly passes this as a full photo upload.

We cache at FULL QUALITY (up to 5 MB) so cover= always looks great.
prep_for_upload() compresses to 200 KB only for the thumb= small preview.
prep_cover() returns the FULL-QUALITY 1280×720 JPEG for cover=.
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
CACHE_MAX  = 500 * 1024 * 1024  # 500 MB cap

_W, _H          = 1280, 720          # legacy 16:9 reference ratio (letterbox calc only)
_COVER_CAP_W    = 3840               # cover= resolution cap — matches Magic Thumbnail's native 4K render
_COVER_CAP_H    = 2160
_THUMB_MAX      = 200 * 1024         # 200 KB — thumb= small preview hard limit
_COVER_MAX      = 5 * 1024 * 1024   # 5 MB  — cover= full-quality HD poster

for _d in (THUMB_DIR, CACHE_DIR, TMP_DIR):
    os.makedirs(_d, exist_ok=True)


# ── User custom thumb ─────────────────────────────────────────

def user_thumb_path(uid: int) -> str:
    return os.path.join(THUMB_DIR, f"{uid}.jpg")


def save_user_thumb(uid: int, src: str) -> str | None:
    """
    Save user's custom thumbnail at full 1280×720 HD quality.
    Stored at high quality (up to 5 MB) — never compressed to 200 KB here.
    prep_for_upload() handles compression at send time.
    """
    dest = user_thumb_path(uid)
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size
        scale  = min(_W / w, _H / h)
        nw, nh = int(w * scale), int(h * scale)
        img    = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
        canvas.paste(img, ((_W - nw) // 2, (_H - nh) // 2))
        # Save at full quality — this is what cover= will use
        canvas.save(dest, "JPEG", quality=95, subsampling=0, optimize=True)
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
    Cache the poster at FULL QUALITY (up to 5 MB) — never compress here.
    The cache stores the original high-quality poster for cover= use.
    prep_for_upload() handles 200 KB compression when building the thumb= preview.
    """
    dest = cache_path(title, year)
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size
        # Scale to 1280×720 keeping aspect ratio, letterbox with black
        scale  = min(_W / w, _H / h)
        nw, nh = int(w * scale), int(h * scale)
        img    = img.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (_W, _H), (0, 0, 0))
        canvas.paste(img, ((_W - nw) // 2, (_H - nh) // 2))

        # Save at full quality — never cap at 200 KB in the cache
        for q in (95, 90, 85):
            canvas.save(dest, "JPEG", quality=q, subsampling=0, optimize=True)
            if os.path.getsize(dest) <= _COVER_MAX:
                break

        size_kb = os.path.getsize(dest) / 1024
        LOGGER.debug(f"[ThumbStore] Cached {title} ({year}) → {size_kb:.0f} KB (full quality)")
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
        img.thumbnail((320, 320), Image.LANCZOS)
        for q in (92, 82, 72, 60):
            img.save(out, "JPEG", quality=q, optimize=True)
            if os.path.getsize(out) <= _THUMB_MAX:
                break
        size_kb = os.path.getsize(out) / 1024
        LOGGER.debug(f"[ThumbStore] thumb= preview: {size_kb:.0f} KB (≤200 KB)")
        return out
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] prep_for_upload (320): {e}")
        return None


def prep_cover(src: str, dest: str | None = None) -> str | None:
    """
    Prepare the HD cover for the cover= parameter.

    Works with BOTH:
      - PyroTGFork send_video(cover=...)   → layer 166+
      - aiogram>=3.18.0 send_video(cover=...) → properly sends as full photo

    The cover= field accepts a full Photo (not a thumbnail), so Telegram
    stores and displays it at full resolution when the video is opened.

    Output: native resolution JPEG at HIGH QUALITY (up to 5 MB), capped at
    3840×2160 (matches the Magic Thumbnail renderers' own 4K output) —
    never upscaled, and never downscaled below what the source already is.
    We do NOT compress to 200 KB here — that limit only applies to thumb=.
    """
    if not src or not os.path.exists(src):
        return None
    out = dest or src.rsplit(".", 1)[0] + "_cover1280.jpg"
    try:
        from PIL import Image
        img    = Image.open(src).convert("RGB")
        w, h   = img.size

        # Only downscale if the source exceeds our cap — never upscale a
        # smaller source (that would just blur it, not add real detail)
        scale = min(_COVER_CAP_W / w, _COVER_CAP_H / h, 1.0)
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        nw, nh = img.size

        # Letterbox only if the aspect ratio meaningfully deviates from
        # 16:9 — our own renderers already output exact 16:9, so this
        # path only fires for odd-shaped user-supplied custom thumbs
        target_ratio = _W / _H
        actual_ratio = nw / nh
        if abs(actual_ratio - target_ratio) > 0.02:
            box_w, box_h = (nw, int(nw / target_ratio)) if actual_ratio > target_ratio \
                else (int(nh * target_ratio), nh)
            canvas = Image.new("RGB", (box_w, box_h), (0, 0, 0))
            canvas.paste(img, ((box_w - nw) // 2, (box_h - nh) // 2))
        else:
            canvas = img

        # Save at FULL QUALITY — this is the movie poster people will see
        # Never compress below the top quality tier unless the 5 MB cap forces it
        for q in (97, 94, 90, 85):
            canvas.save(out, "JPEG", quality=q, subsampling=0, optimize=True)
            sz = os.path.getsize(out)
            if sz <= _COVER_MAX:
                break

        size_kb = os.path.getsize(out) / 1024
        LOGGER.debug(f"[ThumbStore] cover= HD poster: {canvas.size[0]}×{canvas.size[1]} → {size_kb:.0f} KB (full quality)")
        return out
    except Exception as e:
        LOGGER.warning(f"[ThumbStore] prep_cover: {e}")
        return None


# ── Helper ────────────────────────────────────────────────────

def _rm(path: str):
    try: os.remove(path)
    except Exception: pass
