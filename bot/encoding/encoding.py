"""
encoding.py — NXTL FFmpeg Encode Engine  (full rewrite)
=========================================================
Key changes vs old system:
  • -progress pipe:1  →  read from proc.stdout line-by-line (no temp files)
  • -loglevel error   →  stderr only gets actual errors, not stats spam
  • handle_progress() parses every ffmpeg progress key: frame / fps / speed /
    out_time_us / total_size / progress=end
  • Progress card shows: animated bar, %, fps, speed multiplier, ETA,
    elapsed time, current output size, size-reduction vs source
  • Cancel support: checks task_manager.is_cancelled() and kills ffmpeg
  • encode() passes tid through so cancel button works
  • Fixed double proc.communicate() deadlock (was in old code)
  • Fixed bytes-after-decode bug (old code called .decode() twice)
  • CRF auto-adjust based on source bitrate (prevent inflation)
"""

import asyncio
import json
import math
import os
import re
import shlex
import subprocess
import time

from hachoir.metadata import extractMetadata
from hachoir.parser import createParser

import logging
LOGGER = logging.getLogger("encoding")

import config

download_dir = config.DOWNLOAD_DIR
encode_dir   = config.DOWNLOAD_DIR + "_enc"

from bot.encoding.db import enc_db as db
from .display_progress import TimeFormatter


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FFPROBE HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_codec(filepath: str, channel: str = "v:0") -> list:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-select_streams", channel,
            "-show_entries", "stream=codec_name,codec_tag_string",
            "-of", "default=nokey=1:noprint_wrappers=1",
            filepath,
        ], stderr=subprocess.DEVNULL)
        return out.decode().split()
    except Exception as e:
        LOGGER.debug(f"get_codec({channel}): {e}")
        return []


def get_media_streams(filepath: str) -> list:
    try:
        out = subprocess.check_output([
            "ffprobe", "-hide_banner", "-print_format", "json",
            "-show_streams", filepath,
        ], stderr=subprocess.DEVNULL)
        return json.loads(out.decode()).get("streams", [])
    except Exception as e:
        LOGGER.debug(f"get_media_streams: {e}")
        return []


def get_duration(filepath: str) -> int:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
        ], stderr=subprocess.DEVNULL).decode().strip()
        return int(float(out))
    except Exception:
        pass
    try:
        meta = extractMetadata(createParser(filepath))
        if meta and meta.has("duration"):
            return meta.get("duration").seconds
    except Exception:
        pass
    return 0


def get_width_height(filepath: str) -> tuple[int, int]:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", filepath,
        ], stderr=subprocess.DEVNULL).decode().strip()
        w, h = map(int, out.split("x"))
        return w, h
    except Exception:
        pass
    try:
        meta = extractMetadata(createParser(filepath))
        if meta and meta.has("width") and meta.has("height"):
            return meta.get("width"), meta.get("height")
    except Exception:
        pass
    return 1280, 720


def get_thumbnail(in_filename: str, path: str, ttl: float) -> str | None:
    out = os.path.join(path, f"{time.time()}.jpg")
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(ttl), "-i", in_filename,
            "-vframes", "1", "-y", out,
        ], check=True, capture_output=True)
        return out if os.path.isfile(out) else None
    except Exception as e:
        LOGGER.debug(f"get_thumbnail: {e}")
        return None


async def media_info(filepath: str) -> tuple[int, int | None]:
    """Return (total_seconds, bitrate_kbps)."""
    try:
        proc = subprocess.Popen(
            ["ffprobe", "-hide_banner", "-i", filepath],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        out, _ = proc.communicate()
        text   = out.decode()
        dur    = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", text)
        btr    = re.search(r"bitrate:\s*(\d+)", text)
        total  = 0
        if dur:
            total = (
                int(dur.group(1)) * 3600
                + int(dur.group(2)) * 60
                + math.floor(float(dur.group(3)))
            )
        bitrate = int(btr.group(1)) if btr else None
        return total, bitrate
    except Exception:
        return 0, None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SUBTITLE EXTRACTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def extract_subs(filepath: str, msg, user_id: int) -> str | None:
    check = get_codec(filepath, "s:0")
    if not check or check == ["pgs"]:
        return None
    output = os.path.join(encode_dir, f"{msg.id}.ass")
    try:
        subprocess.call(
            ["ffmpeg", "-y", "-i", filepath, "-map", "s:0", output],
            stderr=subprocess.DEVNULL,
        )
        # Extract embedded fonts (MKV attachments)
        try:
            ids = [str(i) for i in range(1, 41)]
            subprocess.call(
                ["mkvextract", "attachments", filepath] + ids,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass
        # Move extracted fonts to system font cache
        try:
            if os.name != "nt":
                font_exts = "ttf otf woff woff2 ttc fon pfb pfa TTC TTF OTF"
                globs     = " ".join(f"*.{e}" for e in font_exts.split())
                subprocess.run(
                    f"mv -f {globs} /usr/share/fonts/ 2>/dev/null && fc-cache -f 2>/dev/null",
                    shell=True,
                )
        except Exception:
            pass
        return output if os.path.isfile(output) else None
    except Exception as e:
        LOGGER.error(f"extract_subs: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROGRESS — card renderer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _bar(pct: float, width: int = 16) -> str:
    filled = int(width * pct / 100)
    empty  = width - filled
    if 0 < filled < width:
        return "▰" * filled + "▶" + "▱" * (empty - 1)
    return "▰" * filled + "▱" * empty


def _fmt_time(s: float) -> str:
    s = int(max(s, 0))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _fmt_size(b: int) -> str:
    if b <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _enc_card(
    name: str,
    pct: float,
    frame: int,
    fps: float,
    speed: float,
    elapsed: float,
    eta: float,
    out_size: int,
    src_size: int,
    tid: str = "",
    stage: str = "ENCODING",
) -> str:
    wm = getattr(config, "WATERMARK", "@NXT_HUB")

    # Compression ratio line
    ratio_line = ""
    if src_size > 0 and out_size > 0:
        saved   = src_size - out_size
        pct_red = saved / src_size * 100
        sign    = "💚 −" if saved >= 0 else "🔴 +"
        ratio_line = f"\n║  ➤ <b>Saved</b>    :  {sign}{abs(pct_red):.1f}%  ({_fmt_size(abs(saved))})"

    cancel_line = f"\n  ✖ Cancel → <code>/c1_{tid.lower()}</code>" if tid else ""

    return (
        f"╔═「 ⚙️ <b>{stage}</b> 」\n"
        f"║\n"
        f"║  🎬 <b>{name}</b>\n"
        f"║\n"
        f"║  <code>{_bar(pct)}</code>  <b>{pct:.1f}%</b>\n"
        f"║\n"
        f"╠═「 📊 <b>STATS</b> 」\n"
        f"║  ➤ <b>Frame</b>   :  {frame:,}  @  {fps:.1f} fps\n"
        f"║  ➤ <b>Speed</b>   :  {speed:.2f}x  realtime\n"
        f"║  ➤ <b>ETA</b>     :  {_fmt_time(eta)}\n"
        f"║  ➤ <b>Elapsed</b> :  {_fmt_time(elapsed)}\n"
        f"║  ➤ <b>Output</b>  :  {_fmt_size(out_size)}"
        f"{ratio_line}\n"
        f"║  ➤ <b>Task</b>    :  <code>#{tid}</code>\n"
        f"╚══════════════════════"
        f"{cancel_line}\n"
        f"  <i>{wm}</i>"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROGRESS — ffmpeg pipe reader
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_kv(line: str, state: dict):
    """Parse one 'key=value' line from ffmpeg -progress output."""
    if "=" not in line:
        return
    k, _, v = line.partition("=")
    k, v = k.strip(), v.strip()
    try:
        if   k == "frame":       state["frame"]    = int(v)
        elif k == "fps":         state["fps"]       = float(v)
        elif k == "speed":
            state["speed"] = float(v.rstrip("x")) if v not in ("N/A", "0x", "") else 0.0
        elif k == "out_time_us": state["out_time_s"] = int(v) / 1_000_000
        elif k == "total_size":  state["out_size"]  = max(int(v), 0)
        elif k == "progress":    state["done"]      = (v == "end")
    except (ValueError, ZeroDivisionError):
        pass


async def handle_progress(
    proc: asyncio.subprocess.Process,
    msg,
    message,
    filepath: str,
    progress_reader: asyncio.StreamReader,
    tid: str = "",
    stage: str = "ENCODING",
):
    """
    Reads ffmpeg -progress pipe:1 line-by-line.
    Edits the Telegram message every UPDATE_INTERVAL seconds.
    Cancels ffmpeg if task_manager marks tid as cancelled.
    """
    from bot.utils.progress import safe_edit
    from bot.core import task_manager as tm

    UPDATE_INTERVAL = 5   # seconds between Telegram edits

    name     = os.path.basename(filepath)
    src_size = os.path.getsize(filepath) if os.path.isfile(filepath) else 0
    total_s  = get_duration(filepath) or 1
    start    = time.time()
    last_upd = 0.0

    state = {
        "frame": 0, "fps": 0.0, "speed": 0.0,
        "out_time_s": 0.0, "out_size": 0, "done": False,
    }

    try:
        async for raw in progress_reader:
            line = raw.decode(errors="replace").strip()
            _parse_kv(line, state)

            # Respect cancel
            if tid and tm.is_cancelled(tid):
                LOGGER.info(f"[Encode] Task {tid} cancelled — killing ffmpeg")
                try: proc.kill()
                except Exception: pass
                return

            if state["done"]:
                break

            now = time.time()
            if now - last_upd < UPDATE_INTERVAL:
                continue
            last_upd = now

            elapsed   = now - start
            pct       = min(state["out_time_s"] * 100 / total_s, 99.5)
            speed     = state["speed"]
            remaining = (total_s - state["out_time_s"]) / speed if speed > 0 else 0

            await safe_edit(msg, _enc_card(
                name=name, pct=pct,
                frame=state["frame"], fps=state["fps"], speed=speed,
                elapsed=elapsed, eta=remaining,
                out_size=state["out_size"], src_size=src_size,
                tid=tid, stage=stage,
            ))

    except asyncio.CancelledError:
        try: proc.kill()
        except Exception: pass
        raise
    except Exception as e:
        LOGGER.debug(f"handle_progress loop: {e}")

    # Final 100% card
    elapsed = time.time() - start
    await safe_edit(msg, _enc_card(
        name=name, pct=100.0,
        frame=state["frame"], fps=state["fps"], speed=state["speed"],
        elapsed=elapsed, eta=0,
        out_size=state["out_size"], src_size=src_size,
        tid=tid, stage=stage,
    ))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BUILD FFMPEG COMMAND
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _build_cmd(filepath: str, output: str, uid: int, subtitles_path: str, force_hardsub: bool) -> list[str]:
    """Assemble the full ffmpeg command list."""

    def spl(s: str) -> list[str]:
        s = s.strip()
        return shlex.split(s) if s else []

    # ── Codec ─────────────────────────────────────────────────
    x265   = await db.get_hevc(uid)
    v_info = get_codec(filepath, "v:0")
    codec  = ("-c:v libx265" if x265 else "-c:v libx264") if v_info else ""
    bits   = await db.get_bits(uid)
    codec += " -pix_fmt yuv420p10le" if bits else " -pix_fmt yuv420p"

    # ── CRF with bitrate-aware auto-adjust ────────────────────
    crf = int(await db.get_crf(uid) or 26)
    try:
        probe    = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=bit_rate",
            "-of", "json", filepath,
        ], stderr=subprocess.DEVNULL).decode()
        src_kbps = int(json.loads(probe)["format"].get("bit_rate", 0)) // 1000
        if src_kbps and src_kbps < 2000 and crf < 28:
            crf = 28   # low-bitrate source — avoid bloat
        elif src_kbps and src_kbps > 8000 and crf > 24:
            crf = 22   # high-bitrate source — compress more
    except Exception:
        pass

    # ── Preset ────────────────────────────────────────────────
    preset_map = {
        "uf": "ultrafast", "sf": "superfast", "vf": "veryfast",
        "f":  "fast",      "m":  "medium",
    }
    preset = f"-preset {preset_map.get(await db.get_preset(uid), 'slow')}"

    # ── Tune ──────────────────────────────────────────────────
    tune      = await db.get_tune(uid)
    tunevideo = "-tune animation" if tune else "-tune film"

    # ── x264-only: CABAC + ref frames ─────────────────────────
    cabac   = "-coder 1" if await db.get_cabac(uid) else "-coder 0"
    rf      = await db.get_reframe(uid)
    reframe = f"-refs {rf}" if rf in ("4", "8", "16") else ""

    if x265:
        video_opts = "-profile:v main -map 0:v? -map_chapters 0 -map_metadata 0"
    else:
        video_opts = f"{cabac} {reframe} -profile:v main -map 0:v? -map_chapters 0 -map_metadata 0"

    # ── Frame rate ────────────────────────────────────────────
    fr_map = {
        "ntsc": "ntsc", "pal": "pal", "film": "film",
        "23.976": "24000/1001", "30": "30", "60": "60",
    }
    fr    = await db.get_frame(uid)
    frame = f"-r {fr_map[fr]}" if fr in fr_map else ""

    # ── Aspect ────────────────────────────────────────────────
    aspect = "-aspect 16:9" if await db.get_aspect(uid) else ""

    # ── Metadata watermark ────────────────────────────────────
    wm = getattr(config, "WATERMARK", "@NXT_HUB").lstrip("@")
    meta_flag = await db.get_metadata_w(uid)
    metadata  = (
        f"-metadata title={wm} -metadata:s:v title={wm} -metadata:s:a title={wm}"
        if meta_flag else ""
    )

    # ── Subtitle soft-copy ────────────────────────────────────
    ex       = await db.get_extensions(uid)
    s        = await db.get_subtitles(uid)
    subs_i   = get_codec(filepath, "s:0")
    has_subs = bool(subs_i) and subs_i != ["pgs"]
    if s and has_subs:
        if   ex == "MP4": subtitles = "-c:s mov_text -c:t copy -map 0:t? -map 0:s?"
        elif ex == "AVI": subtitles = ""
        else:             subtitles = "-c:s copy -c:t copy -map 0:t? -map 0:s?"
    else:
        subtitles = ""

    # ── -vf filter chain ──────────────────────────────────────
    vf = []
    res_map = {
        "1080": "scale=1920:1080", "720": "scale=1280:720",
        "576":  "scale=768:576",   "480": "scale=852:480",
    }
    r = await db.get_resolution(uid)
    if r in res_map:
        vf.append(res_map[r])

    wm_ass = "bot/encoding/extras/watermark.ass"
    if await db.get_watermark(uid) and os.path.isfile(wm_ass):
        vf.append(f"subtitles={wm_ass}")

    h = await db.get_hardsub(uid)
    if force_hardsub:
        h = True
    if h and subtitles_path and os.path.isfile(subtitles_path):
        safe = subtitles_path.replace("'", "\\'").replace(":", "\\:")
        vf.append(f"subtitles='{safe}'")
    elif h and has_subs:
        safe = filepath.replace("'", "\\'").replace(":", "\\:")
        vf.append(f"subtitles='{safe}':si=0")

    # ── Audio ─────────────────────────────────────────────────
    sr_map  = {"44.1K": "-ar 44100", "48K": "-ar 48000"}
    bit_map = {
        "400": "-b:a 400k", "320": "-b:a 320k", "256": "-b:a 256k",
        "224": "-b:a 224k", "192": "-b:a 192k", "160": "-b:a 160k",
        "128": "-b:a 128k",
    }
    sample  = sr_map.get(await db.get_samplerate(uid), "")
    bitrate = bit_map.get(await db.get_bitrate(uid), "")

    a     = await db.get_audio(uid)
    a_i   = get_codec(filepath, "a:0")
    acodec_map = {
        "dd":     f"-c:a ac3 {sample} {bitrate}",
        "aac":    f"-c:a aac {sample} {bitrate}",
        "vorbis": f"-c:a libvorbis {sample} {bitrate}",
        "alac":   f"-c:a alac {sample} {bitrate}",
        "opus":   f"-c:a libopus -vbr on {sample} {bitrate}",
    }
    audio_opts = acodec_map.get(a, "-c:a copy") if a_i else ""
    audio_opts += " -map 0:a?"

    ch_map = {
        "1.0": "-rematrix_maxval 1.0 -ac 1", "2.0": "-rematrix_maxval 1.0 -ac 2",
        "2.1": "-rematrix_maxval 1.0 -ac 3", "5.1": "-rematrix_maxval 1.0 -ac 6",
        "7.1": "-rematrix_maxval 1.0 -ac 8",
    }
    channels = "" if "-c:a copy" in audio_opts else ch_map.get(await db.get_channels(uid), "")

    # ── Assemble ──────────────────────────────────────────────
    cmd = [
        "ffmpeg", "-hide_banner",
        "-loglevel", "error",
        "-progress", "pipe:1",   # progress stream → stdout
        "-hwaccel", "auto",
        "-y", "-i", filepath,
    ]
    cmd += (
        spl(codec) + spl(f"-crf {crf}") + spl(preset) +
        spl(frame) + spl(tunevideo) + spl(aspect) +
        spl(video_opts) + spl(metadata) + spl(subtitles) +
        spl(audio_opts) + spl(channels) +
        ["-threads", "8"]
    )
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd.append(output)
    return cmd


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PUBLIC ENCODE ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def encode(
    filepath: str,
    message,
    msg,
    audio_map:    list | None = None,
    external_sub: str  | None = None,
    tid:          str         = "",
) -> str | None:
    """
    Encode `filepath` with the user's saved settings.
    Returns path to encoded file, or None on failure.
    """
    os.makedirs(encode_dir, exist_ok=True)

    uid     = message.from_user.id
    ex      = await db.get_extensions(uid)
    name    = os.path.splitext(os.path.basename(filepath))[0]
    out_ext = {"MP4": ".mp4", "AVI": ".avi"}.get(ex, ".mkv")
    output  = os.path.join(encode_dir, name + out_ext)

    assert output != filepath, "Input and output are the same file!"

    # ── Subtitle preparation ──────────────────────────────────
    force_hardsub = False
    sub_path      = None

    if external_sub and os.path.isfile(external_sub):
        sub_path      = external_sub
        force_hardsub = True
        LOGGER.info(f"[Encode] External sub: {external_sub}")
    else:
        sub_path = await extract_subs(filepath, msg, uid)

    # ── Handle custom audio mapping ───────────────────────────
    # audio_map is applied separately — patch audio_opts after build
    cmd = await _build_cmd(filepath, output, uid, sub_path or "", force_hardsub)

    if audio_map:
        # Replace generic '-map 0:a?' with specific stream maps + default disposition
        cmd = [c for c in cmd if c != "-map 0:a?"]   # remove generic audio map flag
        # find index of audio codec flag and insert maps after it
        map_args     = [arg for idx in audio_map for arg in ["-map", f"0:{idx}"]]
        disp_args    = ["-disposition:a:0", "default"]
        # append before output file (last element)
        output_file  = cmd.pop()
        cmd          += map_args + disp_args + [output_file]

    LOGGER.info(f"[Encode] Starting: {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,   # -progress pipe:1 lands here
        stderr=asyncio.subprocess.PIPE,   # errors only
    )

    # Drain stderr (errors) + read progress from stdout concurrently
    async def _drain_stderr():
        _, err = await proc.communicate()
        if err:
            decoded = err.decode(errors="replace").strip()
            if decoded:
                LOGGER.error(f"[Encode] ffmpeg: {decoded[:600]}")

    await asyncio.gather(
        handle_progress(
            proc, msg, message, filepath,
            proc.stdout, tid=tid,
        ),
        _drain_stderr(),
    )

    # ── Validate output ───────────────────────────────────────
    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        LOGGER.error(f"[Encode] Output missing or empty: {output}")
        try:    os.remove(output)
        except: pass
        return None

    in_sz  = os.path.getsize(filepath)
    out_sz = os.path.getsize(output)
    ratio  = out_sz / in_sz * 100 if in_sz else 0
    LOGGER.info(
        f"[Encode] ✅ {output} | "
        f"{in_sz//1024//1024} MB → {out_sz//1024//1024} MB ({ratio:.1f}%)"
    )
    return output
