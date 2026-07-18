"""
Image processing utilities.

make_landscape(input_path, output_path)
  — existing function: ffmpeg scale to 1280×720 (for already-landscape images)

portrait_to_landscape(input_path, output_path, title="")
  — NEW: convert a portrait/poster image into a cinematic 1280×720 cover
    using a blurred + darkened background fill and the poster centered on top.
    If title is given, renders it as text at the bottom.
"""

import asyncio
import subprocess
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance


# ── Font paths ───────────────────────────────────────────────────────────────
# Bundled inside the repo (assets/fonts/) so rendering never depends on what
# fonts happen to be installed on the host — this is what broke in production:
# the old hardcoded system path didn't exist there, so Pillow silently fell
# back to its tiny built-in bitmap font (which also can't render "•" or curly
# quotes, hence the missing-glyph boxes).
_ASSETS_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

_FONT_BOLD_CANDIDATES = [
    str(_ASSETS_FONT_DIR / "Poppins-Bold.ttf"),
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_FONT_MEDIUM_CANDIDATES = [
    str(_ASSETS_FONT_DIR / "Poppins-Medium.ttf"),
    "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_FONT_LIGHT_CANDIDATES = [
    str(_ASSETS_FONT_DIR / "Poppins-Light.ttf"),
    "/usr/share/fonts/truetype/google-fonts/Poppins-Light.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

_FONT_BOLD   = _FONT_BOLD_CANDIDATES[0]
_FONT_MEDIUM = _FONT_MEDIUM_CANDIDATES[0]
_FONT_LIGHT  = _FONT_LIGHT_CANDIDATES[0]

_font_cache: dict = {}


def _resolve_font_path(path: str) -> str:
    """Map a preferred font path to whichever candidate in its family
    actually exists on this host, falling back through the chain."""
    for candidates in (_FONT_BOLD_CANDIDATES, _FONT_MEDIUM_CANDIDATES, _FONT_LIGHT_CANDIDATES):
        if path == candidates[0]:
            for c in candidates:
                if Path(c).exists():
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
        # last-ditch fallback — still better than nothing, but every
        # candidate above should exist since Poppins ships in the repo
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ── Final output quality ────────────────────────────────────────────────────
# Every thumbnail type in the bot (Magic, Minimal, plain landscape, logo
# overlay) already resizes with Image.LANCZOS, which is the high-quality
# resampling algorithm. What was missing is an output-sharpening pass —
# the PIL equivalent of ffmpeg's `unsharp` filter — plus saving at a higher
# JPEG quality, so the final crisp/near-lossless look people expect from a
# proper "scale=...:flags=lanczos,unsharp=...,-q:v 1" pipeline is applied
# consistently everywhere a thumbnail is actually written to disk.
def _finish_and_save(img: Image.Image, output_path: str, quality: int = 97) -> None:
    """Apply a final unsharp-mask sharpening pass and save at high JPEG
    quality. Call this instead of a raw .save(...) at the end of every
    thumbnail-composing function."""
    sharpened = img.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3)
    )
    sharpened.save(output_path, "JPEG", quality=quality, optimize=True)


# ── Existing function (kept for backward compat) ───────────────────────────────
def _make_landscape_sync(input_path: str, output_path: str):
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,"
               "crop=1920:1080,unsharp=5:5:1.0:5:5:0.0",
        "-q:v", "1", "-frames:v", "1", "-y", output_path,
    ]
    subprocess.run(cmd, capture_output=True)


async def make_landscape(input_path: str, output_path: str):
    """Scale any image to 1920×1080 (Full HD) via ffmpeg (fast, good for backdrops).

    Runs in a worker thread — subprocess.run() blocks, and running it
    directly on the event loop would freeze the whole bot (every other
    chat, callback, and health check) until ffmpeg finishes.
    """
    await asyncio.to_thread(_make_landscape_sync, input_path, output_path)


# ── New: portrait → landscape ──────────────────────────────────────────────────
def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
               max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
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


def _portrait_to_landscape_sync(input_path: str, output_path: str, title: str = "") -> None:
    """
    Convert a portrait/poster image to a 1920×1080 (Full HD) landscape cover.

    Layout:
      - Background: portrait scaled to fill 1920×1080, then Gaussian-blurred and darkened
      - Foreground: portrait scaled to fit height (with padding), centered
      - Optional: movie title text at the bottom with gradient shadow
    """
    S = 1.5  # scale factor over the old 1280x720 layout -> native 1920x1080
    W, H = int(1280 * S), int(720 * S)

    img = Image.open(input_path).convert("RGB")
    iw, ih = img.size

    # ── Background layer: fill canvas, blur, darken ──────────────────────────
    bg_scale = max(W / iw, H / ih)
    bg_w, bg_h = int(iw * bg_scale), int(ih * bg_scale)
    bg = img.resize((bg_w, bg_h), Image.LANCZOS)

    # Center-crop to the canvas
    bx = (bg_w - W) // 2
    by = (bg_h - H) // 2
    bg = bg.crop((bx, by, bx + W, by + H))

    # Blur
    bg = bg.filter(ImageFilter.GaussianBlur(radius=int(22 * S)))

    # Darken (overlay black at 55% opacity)
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    canvas = Image.blend(bg, dark, alpha=0.55)

    # ── Foreground: sharp portrait centered ───────────────────────────────────
    pad_top    = int(36 * S)
    pad_bottom = int((120 if title else 40) * S)   # leave room for text
    avail_h    = H - pad_top - pad_bottom
    avail_w    = int(W * 0.52)          # max 52% of width for portrait

    fg_scale = min(avail_w / iw, avail_h / ih)
    fg_w, fg_h = int(iw * fg_scale), int(ih * fg_scale)
    fg = img.resize((fg_w, fg_h), Image.LANCZOS)

    # Subtle drop shadow behind the poster
    shadow_offset = int(8 * S)
    shadow = Image.new("RGBA", (fg_w + shadow_offset * 2, fg_h + shadow_offset * 2), (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", (fg_w, fg_h), (0, 0, 0, 180))
    shadow.paste(shadow_layer, (shadow_offset, shadow_offset))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=int(10 * S)))

    canvas_rgba = canvas.convert("RGBA")
    sx = (W - fg_w) // 2 - shadow_offset
    sy = pad_top - shadow_offset + (avail_h - fg_h) // 2
    canvas_rgba.alpha_composite(shadow, dest=(max(0, sx), max(0, sy)))
    canvas_rgba.paste(fg, ((W - fg_w) // 2, pad_top + (avail_h - fg_h) // 2))

    # ── Text overlay ──────────────────────────────────────────────────────────
    if title:
        # Gradient bar at bottom
        grad_h = int(160 * S)
        grad = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for y in range(grad_h):
            alpha = int(220 * (y / (grad_h - 1)) ** 1.5)
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
        canvas_rgba.alpha_composite(grad, dest=(0, H - grad_h))

        draw = ImageDraw.Draw(canvas_rgba)

        # Title
        title_font = _load_font(_FONT_BOLD, int(52 * S))
        lines = _wrap_text(draw, title.upper(), title_font, W - int(120 * S))

        line_h = int(60 * S)
        total_text_h = len(lines) * line_h
        text_y = H - int(30 * S) - total_text_h

        shadow_off = max(1, int(2 * S))
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            lw = bbox[2] - bbox[0]
            lx = (W - lw) // 2
            # Shadow
            draw.text((lx + shadow_off, text_y + shadow_off), line, font=title_font, fill=(0, 0, 0, 180))
            # Main text
            draw.text((lx, text_y), line, font=title_font, fill=(255, 255, 255, 255))
            text_y += line_h

    # ── Save ──────────────────────────────────────────────────────────────────
    _finish_and_save(canvas_rgba, output_path)


async def portrait_to_landscape(input_path: str, output_path: str, title: str = "") -> None:
    """Convert a portrait/poster image to a 1920×1080 (Full HD) landscape cover.

    Runs the actual (pure-CPU, Pillow-only) rendering in a worker thread —
    this used to run directly on the event loop, which meant every user's
    other messages/callbacks (and the bot's own force-sub/health checks)
    stalled for however long the blur + composite work took.
    """
    await asyncio.to_thread(_portrait_to_landscape_sync, input_path, output_path, title)


# ─────────────────────────────────────────────
# Make Thumbnail with Official Movie Logo
# ─────────────────────────────────────────────

def _make_thumbnail_with_logo_sync(
    backdrop_path: str,
    logo_path: str,
    output_path: str,
    position: str = "mid-left",
    gradient: bool = False,
) -> None:
    """
    Composite an official movie logo PNG (transparent background) onto a
    landscape backdrop at 1920×1080 (Full HD), with an optional gradient
    shadow beneath the logo so it pops on any backdrop color.

    Args:
        backdrop_path : path to the landscape backdrop image (any format)
        logo_path     : path to the downloaded logo PNG (transparent BG)
        output_path   : where to save the final JPEG thumbnail
        position      : logo anchor — "center-left", "center-middle", "center-right"
        gradient      : if True, draw a dark gradient behind the logo area
    """
    S = 1.5  # scale factor over the old 1280x720 layout -> native 1920x1080
    W, H = int(1280 * S), int(720 * S)

    # ── 1. Prepare backdrop ──────────────────────────────────────────────────
    bg = Image.open(backdrop_path).convert("RGB")
    bw, bh = bg.size
    scale  = max(W / bw, H / bh)
    bg     = bg.resize((int(bw * scale), int(bh * scale)), Image.LANCZOS)
    bw2, bh2 = bg.size
    bg = bg.crop(((bw2 - W) // 2, (bh2 - H) // 2,
                  (bw2 - W) // 2 + W, (bh2 - H) // 2 + H))

    canvas = bg.convert("RGBA")

    # ── 2. Load & scale logo ─────────────────────────────────────────────────
    logo = Image.open(logo_path).convert("RGBA")
    lw, lh = logo.size

    # Target: logo should be at most 55% of canvas width and 30% of height
    max_logo_w = int(W * 0.55)
    max_logo_h = int(H * 0.30)
    scale_l    = min(max_logo_w / lw, max_logo_h / lh, 1.0)
    new_lw     = int(lw * scale_l)
    new_lh     = int(lh * scale_l)
    logo       = logo.resize((new_lw, new_lh), Image.LANCZOS)

    # ── 3. Compute logo position — full 9-zone grid ──────────────────────────
    #
    #   top-left    top-center    top-right
    #   mid-left    mid-center    mid-right
    #   bot-left    bot-center    bot-right
    #
    pad_x = int(72 * S)    # horizontal padding from canvas edge
    pad_y = int(55 * S)    # vertical padding from canvas edge

    # Horizontal component
    h_part = position.split("-")[-1] if "-" in position else "center"
    if h_part in ("left",):
        lx = pad_x
    elif h_part in ("right",):
        lx = W - new_lw - pad_x
    else:                               # center / middle / centre
        lx = (W - new_lw) // 2

    # Vertical component
    v_part = position.split("-")[0] if "-" in position else "mid"
    if v_part == "top":
        ly = pad_y
    elif v_part == "bot":
        ly = H - new_lh - pad_y
    else:                               # mid / center / middle
        ly = (H - new_lh) // 2

    # ── 4. Optional gradient shadow behind logo ───────────────────────────────
    if gradient:
        grad_h = new_lh + int(100 * S)
        grad_y = max(0, ly - int(50 * S))
        grad   = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
        gd     = ImageDraw.Draw(grad)
        mid    = grad_h // 2
        for row in range(grad_h):
            dist  = abs(row - mid) / max(mid, 1)
            alpha = int(175 * max(0.0, 1.0 - dist ** 1.5))
            gd.line([(0, row), (W, row)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(grad, dest=(0, grad_y))

    # ── 5. Paste logo ─────────────────────────────────────────────────────────
    # Subtle drop-shadow under the logo
    shadow_pad = int(20 * S)
    shadow_off = int(10 * S)
    shadow_layer = Image.new("RGBA", (new_lw + shadow_pad, new_lh + shadow_pad), (0, 0, 0, 0))
    shadow_fill  = Image.new("RGBA", (new_lw, new_lh), (0, 0, 0, 0))
    # Use logo alpha as shadow shape
    r, g, b, a = logo.split()
    shadow_alpha = a.point(lambda p: int(p * 0.55))
    shadow_rgb   = Image.new("RGB", (new_lw, new_lh), (0, 0, 0))
    shadow_img   = Image.merge("RGBA", (shadow_rgb.split()[0], shadow_rgb.split()[1],
                                        shadow_rgb.split()[2], shadow_alpha))
    shadow_layer.paste(shadow_img, (shadow_off, shadow_off))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=int(8 * S)))
    canvas.alpha_composite(shadow_layer, dest=(lx - shadow_off, ly - shadow_off))

    # Paste the actual logo
    canvas.alpha_composite(logo, dest=(lx, ly))

    # ── 6. Save ───────────────────────────────────────────────────────────────
    _finish_and_save(canvas, output_path)


async def make_thumbnail_with_logo(
    backdrop_path: str,
    logo_path: str,
    output_path: str,
    position: str = "mid-left",   # top/mid/bot  ×  left/center/right
    gradient: bool = False,
) -> None:
    """Composite an official movie logo onto a backdrop (see
    _make_thumbnail_with_logo_sync for the full layout description).

    Runs in a worker thread so this CPU-bound Pillow work never blocks the
    event loop — otherwise the bot would appear to "hang" for every other
    chat while one thumbnail was being rendered.
    """
    await asyncio.to_thread(
        _make_thumbnail_with_logo_sync, backdrop_path, logo_path, output_path,
        position, gradient,
    )

import random


# ─────────────────────────────────────────────
# Magic Thumbnail — real stills collage + torn-photo poster card
# ─────────────────────────────────────────────

def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
              max_width: int, max_lines: int) -> list[str]:
    """Wrap text to max_lines, adding an ellipsis on the last line if cut off.

    The cutoff always lands on a whole word, never mid-word — words are
    dropped from the end one at a time until what's left (+ "…") fits.
    Character-by-character trimming only kicks in as a last resort, for
    the rare case where even a single word is wider than max_width.
    """
    lines = _wrap_text(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]

    def fits(s: str) -> bool:
        return draw.textbbox((0, 0), s + "…", font=font)[2] <= max_width

    while last and not fits(last):
        if " " in last:
            last = last.rsplit(" ", 1)[0]
        else:
            break  # down to one word — fall through to char trimming below

    while last and not fits(last):
        last = last[:-1].rstrip()

    lines[-1] = f"{last}…" if last else "…"
    return lines


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop an image to exactly (w, h), covering the box."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale) + 1, int(ih * scale) + 1
    img = img.resize((nw, nh), Image.LANCZOS)
    x = (nw - w) // 2
    y = (nh - h) // 2
    return img.crop((x, y, x + w, y + h))

def _torn_polygon(w: int, h: int, jag: int = 11, step: int = 15) -> list[tuple]:
    """Generate a ragged, torn-paper-edge polygon around a w x h rectangle."""
    pts = []
    x = 0
    while x < w:
        pts.append((x, random.randint(0, jag)))
        x += step
    pts.append((w, 0))
    y = 0
    while y < h:
        pts.append((w - random.randint(0, jag), y))
        y += step
    pts.append((w, h))
    x = w
    while x > 0:
        pts.append((x, h - random.randint(0, jag)))
        x -= step
    pts.append((0, h))
    y = h
    while y > 0:
        pts.append((random.randint(0, jag), y))
        y -= step
    pts.append((0, 0))
    return pts


def _torn_photo(img: Image.Image, target_w: int, target_h: int,
                 rotate_deg: float = -3.0) -> Image.Image:
    """
    Turn a poster image into a torn/ripped-photo cutout with a soft white
    paper rim, returned as a padded RGBA image (no shadow baked in).
    """
    pad = 24
    photo = _cover_crop(img, target_w, target_h)

    mask = Image.new("L", (target_w, target_h), 0)
    ImageDraw.Draw(mask).polygon(_torn_polygon(target_w, target_h), fill=255)

    paper_mask = mask.filter(ImageFilter.MaxFilter(9))  # dilate a few px for the white rim

    canvas_w, canvas_h = target_w + pad * 2, target_h + pad * 2
    layer = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    paper = Image.new("RGBA", (target_w, target_h), (255, 252, 246, 255))
    paper.putalpha(paper_mask)
    layer.alpha_composite(paper, dest=(pad, pad))

    photo_rgba = photo.convert("RGBA")
    photo_rgba.putalpha(mask)
    layer.alpha_composite(photo_rgba, dest=(pad, pad))

    if rotate_deg:
        layer = layer.rotate(rotate_deg, resample=Image.BICUBIC, expand=True)

    return layer


def _shadow_from_alpha(layer: Image.Image, blur: int = 18, opacity: int = 165) -> Image.Image:
    """Build a soft drop-shadow image from an RGBA layer's alpha channel."""
    alpha = layer.split()[-1].point(lambda p: opacity if p > 10 else 0)
    black = Image.new("RGBA", layer.size, (0, 0, 0, 255))
    empty = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow = Image.composite(black, empty, alpha)
    return shadow.filter(ImageFilter.GaussianBlur(blur))


# Common wordy title suffixes, shortened so a long title has a better shot
# at fitting in full instead of needing an ellipsis. Applied only as a
# fallback when the title doesn't fit even at the smallest font size —
# normal-length titles are never touched. Longest phrases first so e.g.
# "Director's Cut Extended Edition" doesn't get double-abbreviated oddly.
_TITLE_ABBREVIATIONS = [
    (r"\bdirector'?s\s+cut\b", "Dir. Cut"),
    (r"\bextended\s+edition\b", "Ext. Ed."),
    (r"\bspecial\s+edition\b", "Spec. Ed."),
    (r"\bcollector'?s\s+edition\b", "Collector's Ed."),
    (r"\banniversary\s+edition\b", "Anniv. Ed."),
    (r"\bunrated\s+edition\b", "Unrated"),
    (r"\btheatrical\s+cut\b", "Theatrical"),
    (r"\bextended\s+cut\b", "Ext. Cut"),
    (r"\bdirector'?s\s+edition\b", "Dir. Ed."),
    (r"\bextended\b", "Ext."),
    (r"\bpart\s+one\b", "Pt. 1"), (r"\bpart\s+two\b", "Pt. 2"),
    (r"\bpart\s+three\b", "Pt. 3"), (r"\bpart\s+four\b", "Pt. 4"),
    (r"\bpart\s+(\d+)\b", r"Pt. \1"),
    (r"\bchapter\s+one\b", "Ch. 1"), (r"\bchapter\s+two\b", "Ch. 2"),
    (r"\bchapter\s+(\d+)\b", r"Ch. \1"),
    (r"\bvolume\s+one\b", "Vol. 1"), (r"\bvolume\s+two\b", "Vol. 2"),
    (r"\bvolume\s+(\d+)\b", r"Vol. \1"),
]


def _abbreviate_title(text: str) -> str:
    """Shorten common wordy edition/part suffixes (case-insensitively).
    Only ever called as a last-resort fallback — see _fit_title_font.
    Callers here always pass an already-uppercased title, so the
    replacement text is upper-cased too to avoid mixed-case output like
    'THE MOVIE Dir. Cut'."""
    upper = text.isupper()
    for pattern, repl in _TITLE_ABBREVIATIONS:
        if upper:
            repl = repl.upper()
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


# A trailing edition/cut/format tag, optionally preceded by a colon, dash,
# or opening parenthesis. Deliberately keyword-gated (not "anything after
# a colon/dash") so real two-part titles like "Dune: Part Two" or
# "Mission: Impossible" are never touched — only genuine edition tags are.
_EDITION_TAIL_RE = re.compile(
    r"[\s:\-–—(]+(?:the\s+)?"
    r"(?:director'?s\s+cut|special\s+edition|unrated(?:\s+edition)?|"
    r"theatrical(?:\s+cut)?|remaster(?:ed)?(?:\s+edition)?|anniversary\s+edition|"
    r"extended(?:\s+(?:cut|edition))?|imax(?:\s+edition)?|4k(?:\s+edition)?|"
    r"uncut|uncensored|redux|"
    r"definitive\s+edition|ultimate\s+edition|collector'?s\s+edition)"
    r"\)?[.\s]*$",
    re.IGNORECASE,
)


def _strip_edition_tail(text: str) -> str:
    """Drop a trailing edition/cut/format tag entirely (e.g. 'Movie -
    Extended Director's Cut' -> 'Movie'), keeping just the main title —
    the cleanest option, tried before abbreviating or truncating anything.
    Only strips text matching a known edition-tag keyword; leaves the
    title untouched if nothing matches (e.g. real subtitles)."""
    stripped = _EDITION_TAIL_RE.sub("", text).rstrip(" :-–—(")
    return stripped if stripped else text


def _title_fallback_candidates(text: str) -> list:
    """Ordered, increasingly-aggressive shortened variants of a title to
    try — only used once the full title doesn't fit even at the smallest
    allowed font size. Order: drop the edition tag entirely, then also
    abbreviate whatever's left (covers titles with no edition tag to
    strip, or ones still too long after stripping)."""
    candidates = []
    stripped = _strip_edition_tail(text)
    if stripped != text:
        candidates.append(stripped)
    abbreviated = _abbreviate_title(stripped)
    if abbreviated != stripped:
        candidates.append(abbreviated)
    return candidates


def _fit_title_font(draw: ImageDraw.ImageDraw, text: str, max_width: int,
                     max_lines: int = 2, start_size: int = 84, min_size: int = 40,
                     font_path: str = _FONT_BOLD) -> tuple:
    """Pick the largest font size (and its wrapped lines) that fits within
    max_width x max_lines — so long titles shrink instead of overflowing."""
    size = start_size
    while size >= min_size:
        font = _load_font(font_path, size)
        lines = _wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= 4

    font = _load_font(font_path, min_size)
    # Still doesn't fit even at the smallest size — try shortened variants
    # (drop a trailing edition tag, then abbreviate what's left) before
    # resorting to an ellipsis.
    best = text
    for candidate in _title_fallback_candidates(text):
        best = candidate
        lines = _wrap_text(draw, candidate, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
    return font, _truncate(draw, best, font, max_width, max_lines)


def _wrap_first_line(draw: ImageDraw.ImageDraw, words: list[str], font: ImageFont.FreeTypeFont,
                      max_width: int) -> tuple[list[str], int]:
    """Greedily fit as many leading words as possible into max_width."""
    line = []
    i = 0
    while i < len(words):
        trial = " ".join(line + [words[i]])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not line:
            line.append(words[i])
            i += 1
        else:
            break
    return line, i


def _fit_title_with_badge(draw: ImageDraw.ImageDraw, text: str, max_width: int, reserve_w: int,
                           max_lines: int = 2, start_size: int = 84, min_size: int = 40,
                           font_path: str = _FONT_BOLD) -> tuple:
    """Like _fit_title_font, but the first line must leave room on its right
    for a rating badge — so a too-long title spills its extra words onto the
    next line instead of overlapping/pushing the badge."""
    words = text.split()
    size = start_size
    while size >= min_size:
        font = _load_font(font_path, size)
        first_max = max(40, max_width - reserve_w)
        line1_words, idx = _wrap_first_line(draw, words, font, first_max)
        remaining = words[idx:]
        lines = [" ".join(line1_words)]
        if remaining:
            lines.extend(_wrap_text(draw, " ".join(remaining), font, max_width))
        if len(lines) <= max_lines:
            return font, lines
        size -= 4

    font = _load_font(font_path, min_size)
    first_max = max(40, max_width - reserve_w)

    # Still doesn't fit even at the smallest size — try shortened variants
    # (drop a trailing edition tag, then abbreviate what's left) before
    # resorting to an ellipsis.
    best_words = words
    for candidate in _title_fallback_candidates(text):
        best_words = candidate.split()
        line1_words, idx = _wrap_first_line(draw, best_words, font, first_max)
        remaining = best_words[idx:]
        lines = [" ".join(line1_words)]
        if remaining:
            lines.extend(_wrap_text(draw, " ".join(remaining), font, max_width))
        if len(lines) <= max_lines:
            return font, lines

    line1_words, idx = _wrap_first_line(draw, best_words, font, first_max)
    remaining = best_words[idx:]
    lines = [" ".join(line1_words)]
    if remaining:
        lines.extend(_truncate(draw, " ".join(remaining), font, max_width, max_lines - 1))
    return font, lines[:max_lines]


def _imdb_badge_width(draw: ImageDraw.ImageDraw, rating_label: str, star_r: int,
                       rating_font: ImageFont.FreeTypeFont, imdb_font: ImageFont.FreeTypeFont,
                       ipad_x: int, S: int) -> int:
    """Measure the total width of the ★ rating + IMDb pill badge."""
    rbbox = draw.textbbox((0, 0), rating_label, font=rating_font)
    rw = rbbox[2] - rbbox[0]
    ibbox = draw.textbbox((0, 0), "IMDb", font=imdb_font)
    iw = ibbox[2] - ibbox[0]
    return (star_r * 2 + 8 * S) + rw + 10 * S + (iw + ipad_x * 2)


def _draw_imdb_rating_badge(draw: ImageDraw.ImageDraw, x: int, cy: int, rating_label: str,
                             star_r: int, rating_font: ImageFont.FreeTypeFont,
                             imdb_font: ImageFont.FreeTypeFont, ipad_x: int, ipad_y: int,
                             S: int) -> int:
    """Draw a ★ rating followed by a black/yellow 'IMDb' pill, vertically
    centered on cy. Returns the x position right after the badge."""
    _draw_star(draw, x + star_r, cy, star_r, (235, 178, 62, 255))
    x += star_r * 2 + 8 * S

    rbbox = draw.textbbox((0, 0), rating_label, font=rating_font)
    rh = rbbox[3] - rbbox[1]
    draw.text((x, cy - rh // 2 - rbbox[1]), rating_label, font=rating_font, fill=(255, 255, 255, 255))
    x += (rbbox[2] - rbbox[0]) + 10 * S

    ibbox = draw.textbbox((0, 0), "IMDb", font=imdb_font)
    iw, ih = ibbox[2] - ibbox[0], ibbox[3] - ibbox[1]
    pill_h = ih + ipad_y * 2
    pill_top = cy - pill_h // 2
    draw.rounded_rectangle([x, pill_top, x + iw + ipad_x * 2, pill_top + pill_h],
                            radius=5 * S, fill=(14, 14, 14, 255))
    draw.text((x + ipad_x, pill_top + ipad_y - ibbox[1]), "IMDb", font=imdb_font, fill=(240, 197, 24, 255))
    return x + iw + ipad_x * 2


def _draw_tomato_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, fresh: bool) -> None:
    """Small Rotten-Tomatoes-style glyph: a red tomato w/ green stem when
    'fresh' (score >= 60%), or a green splat blob when 'rotten' (< 60%)."""
    import math
    if fresh:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(224, 44, 38, 255))
        stem_w = max(2, int(r * 0.35))
        draw.polygon(
            [(cx - stem_w, cy - int(r * 0.85)), (cx + stem_w, cy - int(r * 0.85)),
             (cx, cy - int(r * 1.5))],
            fill=(69, 140, 60, 255),
        )
    else:
        pts = []
        for i in range(8):
            ang = i * math.pi / 4
            rad = r * (0.7 + 0.3 * (i % 2))
            pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        draw.polygon(pts, fill=(107, 142, 35, 255))


def _draw_age_rating_chip(draw: ImageDraw.ImageDraw, x: int, y: int, label: str,
                           font: ImageFont.FreeTypeFont, S: int) -> tuple:
    """Thin bordered box for an age certification (e.g. 'PG-13', 'TV-MA').
    Returns (x_after, chip_height)."""
    pad_x, pad_y = 9 * S, 5 * S
    bbox = draw.textbbox((0, 0), label, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_w, box_h = w + pad_x * 2, h + pad_y * 2
    draw.rounded_rectangle([x, y, x + box_w, y + box_h], radius=4 * S,
                            outline=(255, 255, 255, 220), width=2 * S)
    draw.text((x + pad_x, y + pad_y - bbox[1]), label, font=font, fill=(255, 255, 255, 255))
    return x + box_w, box_h


def _rt_badge_width(draw: ImageDraw.ImageDraw, score_label: str, r: int,
                     font: ImageFont.FreeTypeFont, S: int) -> int:
    bbox = draw.textbbox((0, 0), score_label, font=font)
    return r * 2 + 8 * S + (bbox[2] - bbox[0])


def _draw_rt_badge(draw: ImageDraw.ImageDraw, x: int, cy: int, score_label: str, fresh: bool,
                    r: int, font: ImageFont.FreeTypeFont, S: int) -> int:
    """Draw the 🍅 icon + percentage, vertically centered on cy. Returns x_after."""
    _draw_tomato_icon(draw, x + r, cy, r, fresh)
    x += r * 2 + 8 * S
    color = (235, 90, 80, 255) if fresh else (150, 190, 90, 255)
    bbox = draw.textbbox((0, 0), score_label, font=font)
    th = bbox[3] - bbox[1]
    draw.text((x, cy - th // 2 - bbox[1]), score_label, font=font, fill=color)
    return x + (bbox[2] - bbox[0])


def _draw_text_pop(draw, xy, text, font, fill=(255, 255, 255, 255),
                    stroke_width=3, stroke_fill=(0, 0, 0, 255), shadow_offset=5):
    """Big bold 'pop' text: soft drop shadow + black stroke outline + fill."""
    x, y = xy
    shadow_font = font
    draw.text((x + shadow_offset, y + shadow_offset), text, font=shadow_font, fill=(0, 0, 0, 130))
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def _badge(draw, x, y, label, font, fill, text_fill, pad_x=20, pad_y=11, border=None):
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    w, h = tw + pad_x * 2, th + pad_y * 2
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=fill, outline=border, width=2 if border else 0)
    draw.text((x + pad_x, y + pad_y - bbox[1]), label, font=font, fill=text_fill)
    return x + w + 14


def _rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    return mask


def _draw_star(draw, cx, cy, r, fill):
    """Draw a filled 5-point star centered at (cx, cy) with outer radius r."""
    import math
    pts = []
    for i in range(10):
        ang = math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def _draw_row(draw, x, y, parts, font_default) -> int:
    """Draw a horizontal run of (text, font, fill) tuples starting at x, y."""
    for text, font, fill in parts:
        f = font or font_default
        draw.text((x, y), text, font=f, fill=fill)
        bbox = draw.textbbox((x, y), text, font=f)
        x = bbox[2]
    return x


def _telegram_icon(size: int) -> Image.Image:
    """Draw a small Telegram paper-plane glyph on a round blue badge."""
    s = size * 4  # supersample then downscale for clean anti-aliasing
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.ellipse([0, 0, s - 1, s - 1], fill=(41, 168, 228, 255))
    plane = [
        (s * 0.20, s * 0.53), (s * 0.82, s * 0.20), (s * 0.68, s * 0.80),
        (s * 0.47, s * 0.62), (s * 0.36, s * 0.75), (s * 0.40, s * 0.55),
    ]
    d.polygon(plane, fill=(255, 255, 255, 255))
    d.polygon([plane[0], plane[1], plane[4]], fill=(210, 230, 245, 255))
    icon = icon.resize((size, size), Image.LANCZOS)
    return icon


def _make_magic_thumbnail_sync(
    backdrop_path: str,
    poster_path: str,
    output_path: str,
    title: str,
    overview: str = "",
    brand: str = "NXT_HUB",
    bot_handle: str = "",
    media_type: str = "movie",
    year: str = "",
    rating: float = 0.0,
    genres: list[str] | None = None,
    runtime: str = "",
    age_rating: str = "",
    rotten_tomatoes: str = "",
    seasons: int | None = None,
    custom_channel: str = "",
) -> None:
    """
    Streaming-poster card, rendered at 2560x1440 (2x) at the standard
    1280x720 (16:9) video-thumbnail size, so it fits video players perfectly:
      - the movie's real backdrop still, full-bleed, with a soft, even
        darkening scrim across the whole frame (text side and poster side
        match in brightness — no bright patch behind the poster)
      - the title/description/info block is vertically centered in the frame
      - big bold clean title (auto-sized, up to 2 lines), with a ★ rating +
        black "IMDb" pill placed right after line 1 (long titles spill their
        extra words onto line 2 instead of overlapping the badge)
      - short real description pulled from TMDB (up to 4 lines)
      - one info row: age-rating chip, 🍅 Rotten Tomatoes score, then
        runtime (or "N Seasons" for TV)/genres/year dot-joined
      - a Telegram badge + wordmark, last element in the block (below the
        info row) — "@NXT_HUB" by default, or the user's own custom_channel
        in its place if they've set one via /setchannel (same font/size/icon)
      - a slightly smaller poster on the right as a clean rounded-rect card
        with a thin, even border and a tight soft shadow, vertically
        centered to match the frame
    """
    S = 3  # render scale for HD output — 1280x720 * 3 = 3840x2160 (true 4K)
    W, H = 1280 * S, 720 * S  # standard 16:9 video-thumbnail size
    genres = genres or []

    panel_w, panel_h = 300 * S, 452 * S  # slightly smaller poster card
    ppx = W - panel_w - 44 * S
    ppy = (H - panel_h) // 2  # vertically centered poster
    left_margin = 56 * S
    content_w = ppx - left_margin - 40 * S

    # ── 1. Background: real backdrop, cover-cropped ─────────────────────────
    bg_src = Image.open(backdrop_path).convert("RGB")
    bg = _cover_crop(bg_src, W, H)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=1.2 * S))
    canvas = bg.convert("RGBA")

    # scrim: one flat, even darkening value across the ENTIRE frame — the
    # poster side must match the text side in brightness exactly, so no
    # per-column gradient is used here (that was making the poster side
    # noticeably brighter than the text side).
    base_tint = Image.new("RGBA", (W, H), (4, 4, 6, int(255 * 0.30)))
    canvas.alpha_composite(base_tint)

    extra = 78
    tint = Image.new("RGBA", (W, H), (4, 4, 6, extra))
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
    content_top_limit = 40 * S  # small safety clamp from the very top edge

    # ── 2. IMDb rating badge — measured first so line 1 of the title can
    #      reserve room for it on its right-hand side. ───────────────────────
    star_r = 11 * S
    badge_gap = 16 * S
    rating_font = _load_font(_FONT_BOLD, 30 * S)
    imdb_font = _load_font(_FONT_BOLD, 15 * S)
    ipad_x, ipad_y = 8 * S, 5 * S

    rating_label = ""
    badge_w = 0
    if rating:
        rating_label = f"{rating:.1f}" if isinstance(rating, float) else str(rating)
        badge_w = _imdb_badge_width(draw, rating_label, star_r, rating_font, imdb_font, ipad_x, S)
    reserve_w = (badge_w + badge_gap) if rating else 0

    # ── 3. Title — measured first so the block below can be centered; line 1
    #      leaves room for the rating badge, extra words spill to line 2. ────
    title_font, title_lines = _fit_title_with_badge(draw, title.upper(), content_w, reserve_w,
                                                      max_lines=2, start_size=68 * S, min_size=38 * S)
    title_line_h = title_font.getbbox("Hg")[3] + 4 * S
    title_block_h = title_line_h * len(title_lines)

    # ── 4. Description — measured first, real overview text, never blank ───
    desc = overview.strip() if overview else "No description available for this title."
    ov_font = _load_font(_FONT_MEDIUM, 21 * S)
    ov_lines = _truncate(draw, desc, ov_font, content_w, 4)
    ov_line_h = ov_font.getbbox("Hg")[3] + 6 * S
    desc_block_h = ov_line_h * len(ov_lines)

    # ── 5. Info row — reserved height (chip / 🍅 badge / text, whichever is tallest)
    chip_font = _load_font(_FONT_BOLD, 15 * S)
    rt_font = _load_font(_FONT_BOLD, 20 * S)
    rt_r = 10 * S
    row_h = 34 * S
    if age_rating:
        row_h = max(row_h, chip_font.getbbox("Hg")[3] + 10 * S)
    if rotten_tomatoes:
        row_h = max(row_h, rt_r * 2, rt_font.getbbox("Hg")[3])
    gap_title_desc = 27 * S
    gap_desc_row = 22 * S

    # ── 6. Brand row — Telegram badge + wordmark, now the LAST element in
    #      the content block (below the timeline/genre line). Shows the
    #      user's own custom_channel if they've set one, else "@NXT_HUB". ──
    brand_font = _load_font(_FONT_BOLD, 22 * S)
    brand_icon_size = 24 * S
    brand_row_h = max(brand_icon_size, brand_font.getbbox("Hg")[3])
    gap_row_brand = 26 * S
    custom_channel = (custom_channel or "").strip()

    # ── Vertically center the title + description + info-row + brand block ─
    block_h = (title_block_h + gap_title_desc + desc_block_h + gap_desc_row + row_h
               + gap_row_brand + brand_row_h)
    y = max(content_top_limit, (H - block_h) // 2)

    # ── 3b. Draw title — big, bold, with a soft dark shadow for legibility ──
    for idx, line in enumerate(title_lines):
        draw.text((left_margin + 3 * S, y + 3 * S), line, font=title_font, fill=(0, 0, 0, 190))
        draw.text((left_margin, y), line, font=title_font, fill=(255, 255, 255, 255),
                   stroke_width=1 * S, stroke_fill=(0, 0, 0, 120))
        bbox = draw.textbbox((left_margin, y), line, font=title_font)
        if idx == 0 and rating:
            badge_x = bbox[2] + badge_gap
            badge_cy = (bbox[1] + bbox[3]) // 2
            _draw_imdb_rating_badge(draw, badge_x, badge_cy, rating_label, star_r,
                                     rating_font, imdb_font, ipad_x, ipad_y, S)
        y = bbox[3] + 4 * S
    y += gap_title_desc

    # ── 4b. Draw description — slight stroke gives a semi-bold look without
    #      jumping all the way to the heavier Bold weight (too shouty for
    #      a 4-line paragraph). ──────────────────────────────────────────
    for line in ov_lines:
        draw.text((left_margin + 1 * S, y + 1 * S), line, font=ov_font, fill=(0, 0, 0, 140))
        draw.text((left_margin, y), line, font=ov_font, fill=(225, 225, 230, 255),
                   stroke_width=max(1, S // 2), stroke_fill=(225, 225, 230, 255))
        bbox = draw.textbbox((left_margin, y), line, font=ov_font)
        y = bbox[3] + 6 * S
    y += gap_desc_row

    # ── 5b. Info row: age chip · 🍅 RT score · runtime/seasons · genre · year ─
    dim_font = _load_font(_FONT_MEDIUM, 19 * S)
    rx = left_margin
    ry = y
    row_cy = ry + row_h // 2

    if age_rating:
        rx, _ = _draw_age_rating_chip(draw, rx, ry + (row_h - (chip_font.getbbox("Hg")[3] + 10 * S)) // 2,
                                       age_rating, chip_font, S)
        rx += 16 * S

    if rotten_tomatoes:
        rt_score = 0
        digits = "".join(c for c in rotten_tomatoes if c.isdigit())
        if digits:
            rt_score = int(digits)
        fresh = rt_score >= 60
        rx = _draw_rt_badge(draw, rx, row_cy, rotten_tomatoes, fresh, rt_r, rt_font, S)
        rx += 16 * S

    parts = []
    if media_type != "movie" and seasons:
        parts.append(f"{seasons} Season" + ("s" if seasons != 1 else ""))
    elif runtime:
        parts.append(runtime)
    for g in genres[:2]:
        parts.append(g)
    if year:
        parts.append(str(year))
    dot = "   •   "
    row_text = dot.join(parts)
    if row_text:
        tbbox = dim_font.getbbox("Hg")
        draw.text((rx, row_cy - tbbox[3] // 2), row_text, font=dim_font, fill=(210, 210, 215, 255))
    y += row_h + gap_row_brand

    # ── 6b. Brand row — Telegram badge + wordmark, last element in the
    #      content block (below the timeline/genre line). Shows the user's
    #      own custom_channel if set, else falls back to "@NXT_HUB" — same
    #      font, size, icon, and position either way. ────────────────────────
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

    # ── 7. Poster — clean rounded-rect card, thin even border, tight shadow ─
    px, py = ppx, ppy
    poster_src = Image.open(poster_path).convert("RGB")

    border = 6 * S  # thin, even border on all four sides (slightly thicker than before)
    inner_w, inner_h = panel_w - border * 2, panel_h - border * 2
    radius = 14 * S

    poster_card = _cover_crop(poster_src, inner_w, inner_h)
    # Slight pop — a touch more contrast and saturation so the poster reads
    # a little richer/punchier against the card, without looking over-edited.
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
    canvas.alpha_composite(shadow, dest=(px, py + 6 * S))
    canvas.alpha_composite(frame, dest=(px, py))

    # ── Save ─────────────────────────────────────────────────────────────────
    _finish_and_save(canvas, output_path)


async def make_magic_thumbnail(
    backdrop_path: str,
    poster_path: str,
    output_path: str,
    title: str,
    overview: str = "",
    brand: str = "NXT_HUB",
    bot_handle: str = "",
    media_type: str = "movie",
    year: str = "",
    rating: float = 0.0,
    genres: list[str] | None = None,
    runtime: str = "",
    age_rating: str = "",
    rotten_tomatoes: str = "",
    seasons: int | None = None,
    custom_channel: str = "",
) -> None:
    """Streaming-poster "Magic Thumbnail" card (see _make_magic_thumbnail_sync
    for the full visual spec).

    This is the slowest, heaviest render in the bot (full-frame Gaussian
    blurs + multiple composites at 2560×1440), so it's the one most
    responsible for the bot appearing to "hang" — it used to run directly
    on the event loop and block every other chat/callback/health-check
    until it finished. Offloading it to a worker thread keeps the bot
    responsive to everyone else while one user's card renders.
    """
    await asyncio.to_thread(
        _make_magic_thumbnail_sync, backdrop_path, poster_path, output_path,
        title, overview, brand, bot_handle, media_type, year, rating,
        genres, runtime, age_rating, rotten_tomatoes, seasons, custom_channel,
    )
