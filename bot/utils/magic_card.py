"""
magic_card.py — Auto "Magic Thumbnail" card for every leeched file.

Unlike Auto_thumb's TMDB-driven card, this has no external movie metadata —
every leeched file gets its own card built purely from:
  - a frame extracted from the video itself (ffmpeg), used as BOTH the
    full-bleed backdrop and (re-cropped, portrait) the "poster" panel
  - whatever real file metadata ffprobe can read: duration, resolution/
    quality, size, video/audio codec

Layout intentionally mirrors the reference design:
  - real backdrop, evenly darkened, full-bleed
  - big bold title (parsed clean name), up to 2 lines
  - one info row: quality chip + container chip, then a
    duration • size • codec line
  - Telegram badge + "@NXT_HUB" (or custom channel) brand row
  - a smaller poster-style card on the right — clean rounded-rect,
    thin white border, soft shadow — cropped from the same frame

The finished image is written straight to `dest` and is what the uploader
passes as both `cover=` (HD, full quality) and `thumb=` (compressed to
320x320) when the file is sent — see bot/core/uploader.py.
"""

import os
import re

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

# ── Fonts (system fonts already used elsewhere in NXTL — no bundled assets) ──
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_FONT_REG_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONT_BOLD = _FONT_BOLD_CANDIDATES[0]
_FONT_REG = _FONT_REG_CANDIDATES[0]

_font_cache: dict = {}


def _resolve_font_path(path: str) -> str:
    candidates = _FONT_BOLD_CANDIDATES if path == _FONT_BOLD else _FONT_REG_CANDIDATES
    for c in candidates:
        if os.path.isfile(c):
            return c
    return path


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key in _font_cache:
        return _font_cache[key]
    resolved = _resolve_font_path(path)
    try:
        font = ImageFont.truetype(resolved, size)
    except Exception:
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _finish_and_save(img: Image.Image, output_path: str, quality: int = 97) -> None:
    sharpened = img.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3)
    )
    sharpened.save(output_path, "JPEG", quality=quality, optimize=True)


def _wrap_text(draw, text, font, max_width) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _truncate(draw, text, font, max_width, max_lines) -> list:
    lines = _wrap_text(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]

    def fits(s):
        return draw.textbbox((0, 0), s + "…", font=font)[2] <= max_width

    while last and not fits(last):
        if " " in last:
            last = last.rsplit(" ", 1)[0]
        else:
            break
    while last and not fits(last):
        last = last[:-1].rstrip()
    lines[-1] = f"{last}…" if last else "…"
    return lines


def _fit_title_font(draw, text, max_width, max_lines=2, start_size=68, min_size=38,
                     font_path=_FONT_BOLD) -> tuple:
    size = start_size
    while size >= min_size:
        font = _load_font(font_path, size)
        lines = _wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= 4
    font = _load_font(font_path, min_size)
    return font, _truncate(draw, text, font, max_width, max_lines)


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop an image to exactly (w, h), covering the box."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale) + 1, int(ih * scale) + 1
    img = img.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return img.crop((x, y, x + w, y + h))


def _rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    return mask


def _shadow_from_alpha(layer: Image.Image, blur: int = 18, opacity: int = 165) -> Image.Image:
    alpha = layer.split()[-1].point(lambda p: opacity if p > 10 else 0)
    black = Image.new("RGBA", layer.size, (0, 0, 0, 255))
    empty = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow = Image.composite(black, empty, alpha)
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def _draw_row(draw, x, y, parts, font_default) -> int:
    for text, font, fill in parts:
        f = font or font_default
        draw.text((x, y), text, font=f, fill=fill)
        bbox = draw.textbbox((x, y), text, font=f)
        x = bbox[2]
    return x


def _telegram_icon(size: int) -> Image.Image:
    s = size * 4
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.ellipse([0, 0, s - 1, s - 1], fill=(41, 168, 228, 255))
    plane = [
        (s * 0.20, s * 0.53), (s * 0.82, s * 0.20), (s * 0.68, s * 0.80),
        (s * 0.47, s * 0.62), (s * 0.36, s * 0.75), (s * 0.40, s * 0.55),
    ]
    d.polygon(plane, fill=(255, 255, 255, 255))
    d.polygon([plane[0], plane[1], plane[4]], fill=(210, 230, 245, 255))
    return icon.resize((size, size), Image.LANCZOS)


def _draw_chip(draw, x, y, label, font, S) -> tuple:
    """Thin bordered box (used for quality / container). Returns (x_after, h)."""
    pad_x, pad_y = 9 * S, 5 * S
    bbox = draw.textbbox((0, 0), label, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_w, box_h = w + pad_x * 2, h + pad_y * 2
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=4 * S,
                            outline=(255, 255, 255, 220), width=2 * S)
    draw.text((x + pad_x, y + pad_y - bbox[1]), label, font=font, fill=(255, 255, 255, 255))
    return x + box_w, box_h


# ── Frame extraction ──────────────────────────────────────────────────────
async def extract_frame(video_path: str, dest: str, w: int = 1920, h: int = 1080) -> bool:
    """Grab a single frame at ~30% into the video, scaled+cropped to fully
    cover (w, h) — no letterbox bars, so it works as a clean full-bleed
    backdrop / poster source."""
    from asyncio import create_subprocess_exec
    from asyncio.subprocess import PIPE

    if not video_path or not os.path.exists(video_path):
        return False
    try:
        pr = await create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
            stdout=PIPE, stderr=PIPE,
        )
        out, _ = await pr.communicate()
        dur = float(out.decode().strip() or "0")
    except Exception:
        dur = 0

    seek = max(dur * 0.30, 3.0) if dur > 10 else 1.0
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek), "-i", video_path, "-vframes", "1",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-q:v", "2", "-y", dest,
    ]
    try:
        pr = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        await pr.communicate()
        return os.path.exists(dest) and os.path.getsize(dest) > 2048
    except Exception:
        return False


# ── Metadata helpers ──────────────────────────────────────────────────────
async def probe_media(video_path: str) -> dict:
    """Real, ffprobe-derived metadata — duration, resolution, size, codecs."""
    import json
    from asyncio import create_subprocess_exec
    from asyncio.subprocess import PIPE

    meta = {"duration": "", "quality": "", "size": "", "vcodec": "", "acodec": "", "ext": ""}
    try:
        meta["size"] = _human_size(os.path.getsize(video_path))
        meta["ext"] = os.path.splitext(video_path)[1].lstrip(".").upper()
    except Exception:
        pass

    try:
        pr = await create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=width,height,codec_type,codec_name",
            "-of", "json", video_path, stdout=PIPE, stderr=PIPE,
        )
        out, _ = await pr.communicate()
        data = json.loads(out.decode() or "{}")
        dur = float(data.get("format", {}).get("duration", 0) or 0)
        if dur:
            h_, rem = divmod(int(dur), 3600)
            m_, s_ = divmod(rem, 60)
            meta["duration"] = f"{h_}h {m_}m" if h_ else f"{m_}m {s_}s"
        for st in data.get("streams", []):
            if st.get("codec_type") == "video" and not meta["vcodec"]:
                meta["vcodec"] = (st.get("codec_name") or "").upper()
                height = st.get("height") or 0
                meta["quality"] = f"{height}p" if height else ""
            elif st.get("codec_type") == "audio" and not meta["acodec"]:
                meta["acodec"] = (st.get("codec_name") or "").upper()
    except Exception:
        pass
    return meta


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


# ── Main render ────────────────────────────────────────────────────────────
def _render_sync(frame_path: str, output_path: str, title: str, meta: dict,
                  custom_channel: str = "") -> None:
    S = 2
    W, H = 1280 * S, 720 * S

    panel_w, panel_h = 300 * S, 452 * S
    ppx = W - panel_w - 44 * S
    ppy = (H - panel_h) // 2
    left_margin = 56 * S
    content_w = ppx - left_margin - 40 * S

    # ── Background: the video's own frame, cover-cropped, evenly darkened ──
    bg_src = Image.open(frame_path).convert("RGB")
    bg = _cover_crop(bg_src, W, H)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=1.2 * S))
    canvas = bg.convert("RGBA")

    base_tint = Image.new("RGBA", (W, H), (4, 4, 6, int(255 * 0.30)))
    canvas.alpha_composite(base_tint)
    tint = Image.new("RGBA", (W, H), (4, 4, 6, 78))
    canvas.alpha_composite(tint)

    bgrad = Image.new("L", (1, H))
    for yy in range(H):
        t = yy / (H - 1)
        bgrad.putpixel((0, yy), int(90 * max(0.0, (t - 0.75) / 0.25)))
    bgrad = bgrad.resize((W, H))
    btint = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    btint.putalpha(bgrad)
    canvas.alpha_composite(btint)

    draw = ImageDraw.Draw(canvas)

    # ── Title ────────────────────────────────────────────────────────────
    title_font, title_lines = _fit_title_font(draw, title.upper(), content_w,
                                               max_lines=2, start_size=68 * S, min_size=38 * S)
    title_line_h = title_font.getbbox("Hg")[3] + 4 * S
    title_block_h = title_line_h * len(title_lines)

    # ── Info row (chips + dot line) ─────────────────────────────────────
    chip_font = _load_font(_FONT_BOLD, 16 * S)
    info_font = _load_font(_FONT_REG, 18 * S)
    row_h = max(chip_font.getbbox("Hg")[3] + 10 * S, 22 * S)

    brand_font = _load_font(_FONT_BOLD, 22 * S)
    brand_icon_size = 26 * S
    brand_row_h = brand_icon_size

    gap_title_info = 26 * S
    gap_row_brand = 20 * S

    block_h = title_block_h + gap_title_info + row_h + gap_row_brand + brand_row_h
    y = (H - block_h) // 2

    for line in title_lines:
        draw.text((left_margin, y + 5 * S), line, font=title_font, fill=(0, 0, 0, 130))
        draw.text((left_margin, y), line, font=title_font, fill=(255, 255, 255, 255),
                   stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += title_line_h
    y += gap_title_info

    # ── chips: quality + container ──────────────────────────────────────
    row_cy = y + row_h // 2
    cx = left_margin
    if meta.get("quality"):
        cx, _ = _draw_chip(draw, cx, y, meta["quality"], chip_font, S)
        cx += 14 * S
    if meta.get("ext"):
        cx, _ = _draw_chip(draw, cx, y, meta["ext"], chip_font, S)
        cx += 18 * S

    parts = [p for p in (meta.get("duration"), meta.get("size"),
                          meta.get("vcodec"), meta.get("acodec")) if p]
    row_text = "   •   ".join(parts)
    if row_text:
        tbbox = info_font.getbbox("Hg")
        draw.text((cx, row_cy - tbbox[3] // 2), row_text, font=info_font,
                  fill=(210, 210, 215, 255))
    y += row_h + gap_row_brand

    # ── brand row ────────────────────────────────────────────────────────
    brand_icon = _telegram_icon(brand_icon_size)
    brand_icon_y = y + (brand_row_h - brand_icon_size) // 2
    canvas.alpha_composite(brand_icon, dest=(left_margin, brand_icon_y))
    brand_text_x = left_margin + brand_icon_size + 10 * S
    brand_text_y = y + (brand_row_h - brand_font.getbbox("Hg")[3]) // 2

    if custom_channel:
        segments = [(custom_channel, brand_font, (255, 255, 255))]
    else:
        segments = [
            ("@NXT_", brand_font, (255, 255, 255)),
            ("HUB", brand_font, (235, 178, 62)),
        ]
    _draw_row(draw, brand_text_x, brand_text_y, segments, brand_font)

    # ── Poster panel — same frame, re-cropped to portrait ───────────────
    border = 6 * S
    inner_w, inner_h = panel_w - border * 2, panel_h - border * 2
    radius = 14 * S

    poster_card = _cover_crop(bg_src, inner_w, inner_h)
    poster_card = ImageEnhance.Contrast(poster_card).enhance(1.06)
    poster_card = ImageEnhance.Color(poster_card).enhance(1.12)
    poster_card = poster_card.convert("RGBA")
    poster_card.putalpha(_rounded_mask(inner_w, inner_h, radius - border))

    frame = Image.new("RGBA", (panel_w, panel_h), (0, 0, 0, 0))
    frame_draw = ImageDraw.Draw(frame)
    frame_draw.rounded_rectangle([0, 0, panel_w - 1, panel_h - 1], radius=radius,
                                  fill=(255, 255, 255, 255))
    frame.alpha_composite(poster_card, dest=(border, border))

    shadow = _shadow_from_alpha(frame, blur=14 * S, opacity=130)
    canvas.alpha_composite(shadow, dest=(ppx, ppy + 6 * S))
    canvas.alpha_composite(frame, dest=(ppx, ppy))

    _finish_and_save(canvas, output_path)


async def generate_leech_magic_thumb(video_path: str, dest: str, title: str,
                                      custom_channel: str = "") -> bool:
    """
    Main entry point — builds the Magic Thumbnail card for a leeched video
    using only the video's own extracted frame + real ffprobe metadata
    (no TMDB/API lookups). Returns True and writes `dest` on success.
    """
    import asyncio
    from bot.utils.thumb_store import TMP_DIR as tmp

    os.makedirs(tmp, exist_ok=True)
    frame_path = os.path.join(tmp, f"leechframe_{os.getpid()}_{id(video_path)}.jpg")

    ok = await extract_frame(video_path, frame_path)
    if not ok:
        return False

    meta = await probe_media(video_path)

    try:
        await asyncio.get_event_loop().run_in_executor(
            None, _render_sync, frame_path, dest, title, meta, custom_channel
        )
        return os.path.exists(dest)
    finally:
        try:
            os.remove(frame_path)
        except Exception:
            pass
