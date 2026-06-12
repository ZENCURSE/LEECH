

import asyncio
import json
import math
import os
import re
import subprocess
import time

from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import logging; LOGGER = logging.getLogger("encoding")
import config
download_dir = config.DOWNLOAD_DIR
encode_dir = config.DOWNLOAD_DIR + "_enc"
from bot.encoding.db import enc_db as db
from .display_progress import TimeFormatter


def get_codec(filepath, channel='v:0'):
    try:
        output = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', channel,
                                          '-show_entries', 'stream=codec_name,codec_tag_string', '-of',
                                          'default=nokey=1:noprint_wrappers=1', filepath])
        return output.decode('utf-8').split()
    except subprocess.CalledProcessError as e:
        LOGGER.error(f"ffprobe failed for {filepath}: {e}")
        return []
    except Exception as e:
        LOGGER.error(f"ffprobe exception for {filepath}: {e}")
        return []

def get_media_streams(filepath):
    try:
        cmd = ['ffprobe', '-hide_banner', '-print_format', 'json', '-show_streams', filepath]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return json.loads(output.decode('utf-8')).get('streams', [])
    except Exception as e:
        LOGGER.error(f"Failed to get media streams: {e}")
        return []

async def extract_subs(filepath, msg, user_id):

    path, extension = os.path.splitext(filepath)
    name = os.path.basename(path)
    check = get_codec(filepath, channel='s:0')
    if check == []:
        return None
    elif check == 'pgs':
        return None
    else:
        output = os.path.join(encode_dir, str(msg.id) + '.ass')

    try:
        subprocess.call(['ffmpeg', '-y', '-i', filepath, '-map', 's:0', output])
        # mkvextract might not be in PATH on Windows, handle gracefully
        try:
            subprocess.call(['mkvextract', 'attachments', filepath, '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16',
                            '17', '18', '19', '20', '21', '22', '23', '24', '25', '26', '27', '28', '29', '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40'])
        except FileNotFoundError:
            LOGGER.warning("mkvextract not found, skipping attachments extraction.")
        except Exception as e:
            LOGGER.error(f"mkvextract failed: {e}")

        # Moving fonts is Linux specific and dangerous on Windows to assume /usr/share/fonts/
        # We will only attempt this on Linux-like environments or skip if it fails
        try:
            if os.name != 'nt':
                subprocess.run([f"mv -f *.JFPROJ *.FNT *.PFA *.ETX *.WOFF *.FOT *.TTF *.SFD *.VLW *.VFB *.PFB *.OTF *.GXF *.WOFF2 *.ODTTF *.BF *.CHR *.TTC *.BDF *.FON *.GF *.PMT *.AMFM  *.MF *.PFM *.COMPOSITEFONT *.PF2 *.GDR *.ABF *.VNF *.PCF *.SFP *.MXF *.DFONT *.UFO *.PFR *.TFM *.GLIF *.XFN *.AFM *.TTE *.XFT *.ACFM *.EOT *.FFIL *.PK *.SUIT *.NFTR *.EUF *.TXF *.CHA *.LWFN *.T65 *.MCF *.YTF *.F3F *.FEA *.SFT *.PFT /usr/share/fonts/"], shell=True)
                subprocess.run([f"mv -f *.jfproj *.fnt *.pfa *.etx *.woff *.fot *.ttf *.sfd *.vlw *.vfb *.pfb *.otf *.gxf *.woff2 *.odttf *.bf *.chr *.ttc *.bdf *.fon *.gf *.pmt *.amfm  *.mf *.pfm *.compositefont *.pf2 *.gdr *.abf *.vnf *.pcf *.sfp *.mxf *.dfont *.ufo *.pfr *.tfm *.glif *.xfn *.afm *.tte *.xft *.acfm *.eot *.ffil *.pk *.suit *.nftr *.euf *.txf *.cha *.lwfn *.t65 *.mcf *.ytf *.f3f *.fea *.sft *.pft /usr/share/fonts/ && fc-cache -f"], shell=True)
        except Exception as e:
            LOGGER.warning(f"Font moving failed (likely not supported on this OS): {e}")

        return output
    except Exception as e:
        LOGGER.error(f"Extract subs failed: {e}")
        return None


async def encode(filepath, message, msg, audio_map=None, external_sub=None):

    ex = await db.get_extensions(message.from_user.id)
    path, extension = os.path.splitext(filepath)
    name = os.path.basename(path)

    if ex == 'MP4':
        output_filepathh = os.path.join(encode_dir, name + '.mp4')
    elif ex == 'AVI':
        output_filepathh = os.path.join(encode_dir, name + '.avi')
    else:
        output_filepathh = os.path.join(encode_dir, name + '.mkv')

    output_filepath = output_filepathh
    subtitles_path = os.path.join(encode_dir, str(msg.id) + '.ass')

    # If external subtitle provided (via /encsub), use it directly
    # and force hardsub ON — user explicitly uploaded a sub file
    _force_hardsub = False
    if external_sub and os.path.isfile(external_sub):
        subtitles_path = external_sub
        _force_hardsub = True
        LOGGER.info(f'Using external subtitle: {external_sub}')
    else:
        subtitles_path = await extract_subs(filepath, msg, message.from_user.id) or subtitles_path

    # Use a per-message unique progress file to avoid collisions
    _enc_tmp = os.path.join(download_dir, f"enc_{msg.id}")
    os.makedirs(_enc_tmp, exist_ok=True)
    progress = os.path.join(_enc_tmp, "process.txt")
    status   = os.path.join(_enc_tmp, "status.json")
    with open(progress, 'w') as f:
        pass

    assert(output_filepath != filepath)

    # Check Path
    if os.path.isfile(output_filepath):
        LOGGER.warning(f'"{output_filepath}": file already exists')
    else:
        LOGGER.info(filepath)

    # HEVC Encode
    x265 = await db.get_hevc(message.from_user.id)
    video_i = get_codec(filepath, channel='v:0')
    if video_i == []:
        codec = ''
    else:
        if x265:
            codec = '-c:v libx265'
        else:
            codec = '-c:v libx264'

    # Tune Encode
    tune = await db.get_tune(message.from_user.id)
    if tune:
        tunevideo = '-tune animation'
    else:
        tunevideo = '-tune film'

    # CABAC
    cbb = await db.get_cabac(message.from_user.id)
    if cbb:
        cabac = '-coder 1'
    else:
        cabac = '-coder 0'

    # Reframe
    rf = await db.get_reframe(message.from_user.id)
    if rf == '4':
        reframe = '-refs 4'
    elif rf == '8':
        reframe = '-refs 8'
    elif rf == '16':
        reframe = '-refs 16'
    else:
        reframe = ''

    # Bits
    b = await db.get_bits(message.from_user.id)
    if not b:
        codec += ' -pix_fmt yuv420p'
    else:
        codec += ' -pix_fmt yuv420p10le'

    # CRF — smart default based on source bitrate to avoid size inflation
    # Lower CRF = better quality but BIGGER file (0=lossless, 51=worst)
    # Rule: if source is already compressed at low bitrate, use higher CRF
    # to avoid re-encoding to a bigger file than the original
    crf = await db.get_crf(message.from_user.id)
    if not crf:
        crf = 26
        await db.set_crf(message.from_user.id, crf)

    # Auto-adjust: if source bitrate < 2 Mbps, bump CRF up to avoid inflation
    try:
        import subprocess as _sp, json as _json
        _probe = _sp.check_output([
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=bit_rate',
            '-of', 'json', filepath
        ]).decode()
        _src_kbps = int(_json.loads(_probe)['format'].get('bit_rate', 0)) // 1000
        if _src_kbps and _src_kbps < 2000 and crf < 28:
            crf = 28   # prevent inflating low-bitrate sources
        elif _src_kbps and _src_kbps > 8000 and crf > 24:
            crf = 22   # high-bitrate source — compress more aggressively
    except Exception:
        pass

    Crf = f'-crf {crf}'

    # Frame
    fr = await db.get_frame(message.from_user.id)
    if fr == 'ntsc':
        frame = '-r ntsc'
    elif fr == 'pal':
        frame = '-r pal'
    elif fr == 'film':
        frame = '-r film'
    elif fr == '23.976':
        frame = '-r 24000/1001'
    elif fr == '30':
        frame = '-r 30'
    elif fr == '60':
        frame = '-r 60'
    else:
        frame = ''

    # Aspect ratio
    ap = await db.get_aspect(message.from_user.id)
    if ap:
        aspect = '-aspect 16:9'
    else:
        aspect = ''

    # Preset
    p = await db.get_preset(message.from_user.id)
    if p == 'uf':
        preset = '-preset ultrafast'
    elif p == 'sf':
        preset = '-preset superfast'
    elif p == 'vf':
        preset = '-preset veryfast'
    elif p == 'f':
        preset = '-preset fast'
    elif p == 'm':
        preset = '-preset medium'
    else:
        preset = '-preset slow'

    # Some Optional Things
    x265 = await db.get_hevc(message.from_user.id)
    if x265:
        video_opts = f'-profile:v main  -map 0:v? -map_chapters 0 -map_metadata 0'
    else:
        video_opts = f'{cabac} {reframe} -profile:v main  -map 0:v? -map_chapters 0 -map_metadata 0'

    # Metadata Watermark
    m = await db.get_metadata_w(message.from_user.id)
    if m:
        metadata = '-metadata title=Cantarellabots -metadata:s:v title=Cantarellabots -metadata:s:a title=Cantarellabots'
    else:
        metadata = ''


    # ── Subtitle handling ─────────────────────────────────────────
    # Hardsub  = burn subtitle into video via -vf subtitles= (visible always)
    # Softsub  = copy subtitle stream alongside video (user can toggle in player)
    # Both can be active simultaneously — hardsub burns a copy, softsub keeps stream
    h      = await db.get_hardsub(message.from_user.id)
    if _force_hardsub:
        h = True   # external sub was provided — always burn it in
    s      = await db.get_subtitles(message.from_user.id)
    subs_i = get_codec(filepath, channel='s:0')
    has_subs = subs_i not in ([], None, 'pgs')   # pgs = bitmap subs, can't burn

    # Softsub stream copy (independent of hardsub)
    if s and has_subs:
        if ex == 'MP4':
            subtitles = '-c:s mov_text -c:t copy -map 0:t? -map 0:s?'
        elif ex == 'AVI':
            subtitles = ''   # AVI has no subtitle container support
        else:
            subtitles = '-c:s copy -c:t copy -map 0:t? -map 0:s?'
    else:
        subtitles = ''

    # ── -vf filter chain: scale → watermark → hardsub ─────────
    # Built as a list then joined with commas — clean, no broken string concat
    vf_filters = []

    r = await db.get_resolution(message.from_user.id)
    if r == '1080':
        vf_filters.append('scale=1920:1080')
    elif r == '720':
        vf_filters.append('scale=1280:720')
    elif r == '576':
        vf_filters.append('scale=768:576')
    elif r == '480':
        vf_filters.append('scale=852:480')
    # OG = source resolution, no scale filter

    w = await db.get_watermark(message.from_user.id)
    wm_ass = 'bot/encoding/extras/watermark.ass'
    if w and os.path.isfile(wm_ass):
        vf_filters.append(f'subtitles={wm_ass}')

    # Hardsub — burn subtitle track into video frames
    # Requires: extracted .ass file from extract_subs(), or falls back to stream index
    if h and has_subs:
        if subtitles_path and os.path.isfile(subtitles_path):
            # Use extracted .ass — includes custom fonts/styles
            vf_filters.append(
                f"subtitles='{subtitles_path}'"
            )
        else:
            # Fallback: burn directly from embedded stream (index 0:s:0)
            safe_path = filepath.replace("'", "\\'").replace(":", "\\:")
            vf_filters.append(f"subtitles='{safe_path}':si=0")

    watermark = ('-vf ' + ','.join(vf_filters)) if vf_filters else ''


    # Sample rate
    sr = await db.get_samplerate(message.from_user.id)
    if sr == '44.1K':
        sample = '-ar 44100'
    elif sr == '48K':
        sample = '-ar 48000'
    else:
        sample = ''

    # bit rate
    bit = await db.get_bitrate(message.from_user.id)
    if bit == '400':
        bitrate = '-b:a 400k'
    elif bit == '320':
        bitrate = '-b:a 320k'
    elif bit == '256':
        bitrate = '-b:a 256k'
    elif bit == '224':
        bitrate = '-b:a 224k'
    elif bit == '192':
        bitrate = '-b:a 192k'
    elif bit == '160':
        bitrate = '-b:a 160k'
    elif bit == '128':
        bitrate = '-b:a 128k'
    else:
        bitrate = ''

    # Audio
    a = await db.get_audio(message.from_user.id)
    a_i = get_codec(filepath, channel='a:0')
    if a_i == []:
        audio_opts = ''
    else:
        if a == 'dd':
            audio_opts = f'-c:a ac3 {sample} {bitrate}'
        elif a == 'aac':
            audio_opts = f'-c:a aac {sample} {bitrate}'
        elif a == 'vorbis':
            audio_opts = f'-c:a libvorbis {sample} {bitrate}'
        elif a == 'alac':
            audio_opts = f'-c:a alac {sample} {bitrate}'
        elif a == 'opus':
            audio_opts = f'-c:a libopus -vbr on {sample} {bitrate}'
        else:
            audio_opts = '-c:a copy'

        if audio_map:
            # If audio_map is provided (e.g. [0:1, 0:2]), we use it to map audio streams.
            # We need to make sure we map all audio streams in the desired order.
            # The audio_opts above sets the codec for all audio streams.
            # We need to construct the map part.
            # Note: The previous code had `-map 0:a?` attached to audio_opts.
            # If we have specific mapping, we shouldn't use generic `-map 0:a?`.

            # The `audio_map` contains indices of audio streams in the original file.
            # e.g. [1, 2] means map 0:1 then map 0:2.

            map_opts = ""
            for idx in audio_map:
                map_opts += f" -map 0:{idx}"

            # Explicitly set the default disposition for the first audio stream in the new order
            # This ensures the first audio track in the list is the default one
            disposition_opts = " -disposition:a:0 default"

            audio_opts = f"{audio_opts} {map_opts} {disposition_opts}"
        else:
             audio_opts += " -map 0:a?"


    # Audio Channel
    c = await db.get_channels(message.from_user.id)
    if '-c:a copy' in audio_opts:
        channels = ''
    elif c == '1.0':
        channels = '-rematrix_maxval 1.0 -ac 1'
    elif c == '2.0':
        channels = '-rematrix_maxval 1.0 -ac 2'
    elif c == '2.1':
        channels = '-rematrix_maxval 1.0 -ac 3'
    elif c == '5.1':
        channels = '-rematrix_maxval 1.0 -ac 6'
    elif c == '7.1':
        channels = '-rematrix_maxval 1.0 -ac 8'
    else:
        channels = ''

    finish = '-threads 8'

    import shlex as _shlex
    # Finally
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error',
               '-progress', progress, '-hwaccel', 'auto', '-y', '-i', filepath]
    # Use shlex.split for all args EXCEPT watermark — watermark contains a
    # quoted subtitles= filter with file paths that must NOT be word-split.
    # We split watermark manually: extract -vf and its value as two clean args.
    def _safe_split(s):
        s = s.strip()
        if not s:
            return []
        try:
            return _shlex.split(s)
        except ValueError:
            return s.split()

    command.extend(
        _safe_split(codec) + _safe_split(preset) + _safe_split(frame) +
        _safe_split(tunevideo) + _safe_split(aspect) + _safe_split(video_opts) +
        _safe_split(Crf) + _safe_split(metadata) + _safe_split(subtitles) +
        _safe_split(audio_opts) + _safe_split(channels) + _safe_split(finish)
    )
    # Add watermark/-vf as separate properly-quoted args
    if watermark:
        # watermark is like: '-vf scale=1280:720,subtitles=\'path\''
        # split only on the first space to get ['-vf', 'filter_value']
        vf_parts = watermark.split(' ', 1)
        command.extend(vf_parts)

    proc = await asyncio.create_subprocess_exec(
        *command, output_filepath,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Run progress polling and proc.communicate() concurrently
    # Previously handle_progress ran BEFORE communicate() which meant
    # proc.returncode was always None and the loop never saw 'end'
    stdout_bytes, stderr_bytes = b'', b''
    async def _wait():
        nonlocal stdout_bytes, stderr_bytes
        stdout_bytes, stderr_bytes = await proc.communicate()

    await asyncio.gather(
        handle_progress(proc, msg, message, filepath, progress, status),
        _wait(),
    )
    stdout, stderr = stdout_bytes, stderr_bytes
    stdout, stderr = stdout.decode().strip(), stderr.decode().strip()
    e_response = stderr.decode().strip()
    t_response = stdout.decode().strip()
    LOGGER.error(f"FFmpeg stderr: {e_response}")
    if t_response:
        LOGGER.info(f"FFmpeg stdout: {t_response}")
    await proc.communicate()

    if not os.path.isfile(output_filepath) or os.path.getsize(output_filepath) == 0:
        LOGGER.error(f"Encoding failed: {output_filepath} not created or is 0 bytes.")
        if os.path.isfile(output_filepath):
            os.remove(output_filepath)
        return None

    return output_filepath


def get_thumbnail(in_filename, path, ttl):
    out_filename = os.path.join(path, str(time.time()) + ".jpg")
    try:
        # ffmpeg -ss <ttl> -i <in_filename> -vframes 1 -y <out_filename>
        command = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-ss', str(ttl),
            '-i', in_filename,
            '-vframes', '1',
            '-y', out_filename
        ]
        subprocess.run(command, check=True, capture_output=True)
        if os.path.isfile(out_filename):
            return out_filename
        else:
            LOGGER.warning(f"Thumbnail file not created: {out_filename}")
            return None
    except subprocess.CalledProcessError as e:
        LOGGER.warning(f"Thumbnail generation failed (CalledProcessError): {e.stderr.decode().strip() if e.stderr else e}")
        return None
    except Exception as e:
        LOGGER.warning(f"Thumbnail generation failed: {e}")
        return None


def get_duration(filepath):
    try:
        # Try using ffprobe first
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration', '-of',
            'default=noprint_wrappers=1:nokey=1', filepath
        ]
        output = subprocess.check_output(cmd).decode('utf-8').strip()
        return int(float(output))
    except Exception as e:
        LOGGER.warning(f"ffprobe duration failed: {e}, falling back to hachoir")
        try:
            metadata = extractMetadata(createParser(filepath))
            if metadata and metadata.has("duration"):
                return metadata.get('duration').seconds
        except Exception as e:
            LOGGER.error(f"hachoir duration failed: {e}")
    return 0


def get_width_height(filepath):
    try:
        # Try using ffprobe first
        cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of',
            'csv=s=x:p=0', filepath
        ]
        output = subprocess.check_output(cmd).decode('utf-8').strip()
        width, height = map(int, output.split('x'))
        return width, height
    except Exception as e:
        LOGGER.warning(f"ffprobe width/height failed: {e}, falling back to hachoir")
        try:
            metadata = extractMetadata(createParser(filepath))
            if metadata and metadata.has("width") and metadata.has("height"):
                return metadata.get("width"), metadata.get("height")
        except Exception as e:
            LOGGER.error(f"hachoir width/height failed: {e}")
    return (1280, 720)


async def media_info(saved_file_path):
    process = subprocess.Popen(
        [
            'ffmpeg',
            "-hide_banner",
            '-i',
            saved_file_path
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    stdout, stderr = process.communicate()
    output = stdout.decode().strip()
    duration = re.search(r"Duration:\s*(\d*):(\d*):(\d+\.?\d*)[\s\w*$]", output)
    bitrates = re.search(r"bitrate:\s*(\d+)[\s\w*$]", output)

    if duration is not None:
        hours = int(duration.group(1))
        minutes = int(duration.group(2))
        seconds = math.floor(float(duration.group(3)))
        total_seconds = (hours * 60 * 60) + (minutes * 60) + seconds
    else:
        total_seconds = None
    if bitrates is not None:
        bitrate = bitrates.group(1)
    else:
        bitrate = None
    return total_seconds, bitrate


async def handle_progress(proc, msg, message, filepath,
                            progress_file: str, status_file: str):
    """
    Poll ffmpeg -progress output and update the Telegram message card.
    Fixes:
      - Correct file paths (no broken string concat)
      - MESSAGE_NOT_MODIFIED guard (skip edit when text unchanged)
      - Clean NXTL-style progress card with bar, %, ETA, speed, elapsed
    """
    import config as _cfg
    SEP = "━━━━━━━━━━━━━━━━━━━━━━━━"
    name = os.path.basename(filepath)
    stem = (name[:36] + "…") if len(name) > 38 else name
    COMPRESSION_START_TIME = time.time()
    last_text = ""

    # Write initial status json
    try:
        with open(status_file, 'w') as f:
            json.dump({'running': True, 'pid': proc.pid,
                       'message': msg.id, 'user': message.from_user.id}, f)
    except Exception:
        pass

    total_time, _ = await media_info(filepath)
    if not total_time or total_time <= 0:
        total_time = 1

    while proc.returncode is None:
        await asyncio.sleep(4)
        try:
            with open(progress_file, 'r') as file:
                text = file.read()
        except Exception:
            continue

        time_in_us = re.findall(r"out_time_ms=(\d+)", text)
        speed_list = re.findall(r"speed=(\d+\.?\d*)", text)
        prog_list  = re.findall(r"progress=(\w+)", text)

        if prog_list and prog_list[-1] == "end":
            break

        elapsed_enc  = time.time() - COMPRESSION_START_TIME
        elapsed_media = int(time_in_us[-1]) / 1_000_000 if time_in_us else 0
        speed_val     = float(speed_list[-1]) if speed_list else 0.0

        pct = min(int(elapsed_media * 100 / total_time), 99)
        filled = int(pct / 10)
        bar    = "█" * filled + "░" * (10 - filled)

        if speed_val > 0:
            remaining = max(total_time - elapsed_media, 0)
            eta_secs  = int(remaining / speed_val)
            mm, ss    = divmod(eta_secs, 60)
            hh, mm    = divmod(mm, 60)
            eta_str   = f"{hh}h {mm}m {ss}s" if hh else (f"{mm}m {ss}s" if mm else f"{ss}s")
        else:
            eta_str = "—"

        em, es = divmod(int(elapsed_enc), 60)
        eh, em = divmod(em, 60)
        elapsed_str = f"{eh}h {em}m {es}s" if eh else (f"{em}m {es}s" if em else f"{es}s")

        new_text = (
            f"<b>{SEP}</b>\n"
            f"<b>⚙️  ENCODING</b>\n"
            f"<b>{SEP}</b>\n\n"
            f"🎬 <b>{stem}</b>\n\n"
            f"<b><code>{bar}</code>  {pct}%</b>\n\n"
            f"⚡ <b>Speed:</b> {speed_val:.2f}x\n"
            f"⏱ <b>Elapsed:</b> {elapsed_str}\n"
            f"🕐 <b>ETA:</b> {eta_str}\n\n"
            f"<b>{SEP}</b>\n"
            f"<b>⚡ {_cfg.WATERMARK}</b>"
        )

        # Skip edit if text unchanged — avoids MESSAGE_NOT_MODIFIED
        if new_text == last_text:
            continue
        last_text = new_text

        try:
            await msg.edit_text(new_text, parse_mode="html")
        except Exception:
            pass

