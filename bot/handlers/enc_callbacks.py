"""
Encoding settings callbacks — ported from ENCODING-BOT, integrated into NXTL.
Handles all 'trigger*' and settings navigation callback_data values.
"""
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery

from bot.encoding.db import enc_db as db
from bot.encoding.settings_utils import (
    OpenSettings, VideoSettings, AudioSettings, ExtraSettings
)


@Client.on_callback_query(filters.regex(
    r"^(closeMeh|VideoSettings|AudioSettings|ExtraSettings|OpenSettings"
    r"|Watermark|triggerMode|triggerUploadMode|triggerMetadata|triggerVideo"
    r"|triggerHardsub|triggerSubtitles|triggerextensions|triggerframe"
    r"|triggerPreset|triggersamplerate|triggerbitrate|triggerAudioCodec"
    r"|triggerAudioChannels|triggerResolution|triggerBits|triggerHevc"
    r"|triggertune|triggerreframe|triggercabac|triggeraspect|triggerCRF"
    r"|audiosel.*)$"
))
async def enc_callback(client: Client, cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data

    if data == "closeMeh":
        await cb.message.delete()
        return

    if data == "OpenSettings":
        await OpenSettings(cb.message, user_id=uid)
    elif data == "VideoSettings":
        await VideoSettings(cb.message, user_id=uid)
    elif data == "AudioSettings":
        await AudioSettings(cb.message, user_id=uid)
    elif data == "ExtraSettings":
        await ExtraSettings(cb.message, user_id=uid)
    elif data == "Watermark":
        await cb.answer("Label button — use buttons below it.", show_alert=True)
        return

    elif data == "triggerMode":
        v = await db.get_drive(uid)
        await db.set_drive(uid, not v)
        await ExtraSettings(cb.message, user_id=uid)

    elif data == "triggerUploadMode":
        v = await db.get_upload_as_doc(uid)
        await db.set_upload_as_doc(uid, not v)
        await ExtraSettings(cb.message, user_id=uid)

    elif data == "triggerMetadata":
        v = await db.get_metadata_w(uid)
        await db.set_metadata_w(uid, not v)
        await ExtraSettings(cb.message, user_id=uid)

    elif data == "triggerVideo":
        v = await db.get_watermark(uid)
        await db.set_watermark(uid, not v)
        await ExtraSettings(cb.message, user_id=uid)

    elif data == "triggerHardsub":
        v = await db.get_hardsub(uid)
        await db.set_hardsub(uid, not v)
        await ExtraSettings(cb.message, user_id=uid)

    elif data == "triggerSubtitles":
        v = await db.get_subtitles(uid)
        await db.set_subtitles(uid, not v)
        await ExtraSettings(cb.message, user_id=uid)

    elif data == "triggerextensions":
        ex = await db.get_extensions(uid)
        cycle = {"MP4": "MKV", "MKV": "AVI", "AVI": "MP4"}
        await db.set_extensions(uid, cycle.get(ex, "MP4"))
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggerframe":
        fr = await db.get_frame(uid)
        cycle = {"source": "ntsc", "ntsc": "pal", "pal": "film",
                 "film": "23.976", "23.976": "30", "30": "60", "60": "source"}
        await db.set_frame(uid, cycle.get(fr, "source"))
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggerPreset":
        p = await db.get_preset(uid)
        cycle = {"uf": "sf", "sf": "vf", "vf": "f", "f": "m", "m": "s", "s": "uf"}
        await db.set_preset(uid, cycle.get(p, "sf"))
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggersamplerate":
        sr = await db.get_samplerate(uid)
        cycle = {"44.1K": "48K", "48K": "source", "source": "44.1K"}
        await db.set_samplerate(uid, cycle.get(sr, "44.1K"))
        await AudioSettings(cb.message, user_id=uid)

    elif data == "triggerbitrate":
        bit = await db.get_bitrate(uid)
        cycle = {"400": "320", "320": "256", "256": "224", "224": "192",
                 "192": "160", "160": "128", "128": "source", "source": "400"}
        await db.set_bitrate(uid, cycle.get(bit, "128"))
        await AudioSettings(cb.message, user_id=uid)

    elif data == "triggerAudioCodec":
        a = await db.get_audio(uid)
        cycle = {"dd": "copy", "copy": "aac", "aac": "opus",
                 "opus": "alac", "alac": "vorbis", "vorbis": "dd"}
        await db.set_audio(uid, cycle.get(a, "aac"))
        await AudioSettings(cb.message, user_id=uid)

    elif data == "triggerAudioChannels":
        c = await db.get_channels(uid)
        cycle = {"source": "1.0", "1.0": "2.0", "2.0": "2.1",
                 "2.1": "5.1", "5.1": "7.1", "7.1": "source"}
        if c == "5.1":
            await cb.answer("7.1 is for Blu-ray only.", show_alert=True)
        await db.set_channels(uid, cycle.get(c, "source"))
        await AudioSettings(cb.message, user_id=uid)

    elif data == "triggerResolution":
        r = await db.get_resolution(uid)
        cycle = {"OG": "1080", "1080": "720", "720": "480", "480": "576", "576": "OG"}
        await db.set_resolution(uid, cycle.get(r, "OG"))
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggerBits":
        b = await db.get_bits(uid)
        hevc = await db.get_hevc(uid)
        if not hevc and not b:
            await cb.answer("H.264 doesn't support 10-bit in this bot.", show_alert=True)
        else:
            await db.set_bits(uid, not b)
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggerHevc":
        v = await db.get_hevc(uid)
        if not v:
            await cb.answer("H.265 takes longer to encode.", show_alert=True)
        await db.set_hevc(uid, not v)
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggertune":
        v = await db.get_tune(uid)
        await db.set_tune(uid, not v)
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggerreframe":
        rf = await db.get_reframe(uid)
        cycle = {"pass": "4", "4": "8", "8": "16", "16": "pass"}
        if rf == "8":
            await cb.answer("Reframe 16 may not be supported everywhere.", show_alert=True)
        await db.set_reframe(uid, cycle.get(rf, "pass"))
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggercabac":
        v = await db.get_cabac(uid)
        await db.set_cabac(uid, not v)
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggeraspect":
        v = await db.get_aspect(uid)
        if not v:
            await cb.answer("Forces video to 16:9 aspect ratio.", show_alert=True)
        await db.set_aspect(uid, not v)
        await VideoSettings(cb.message, user_id=uid)

    elif data == "triggerCRF":
        crf = await db.get_crf(uid)
        next_crf = int(crf) + 1
        if next_crf > 30:
            next_crf = 18
        await db.set_crf(uid, next_crf)
        await VideoSettings(cb.message, user_id=uid)

    elif data.startswith("audiosel"):
        try:
            from bot.encoding.video_utils.audio_selector import sessions
            if uid in sessions:
                await sessions[uid].resolve_callback(cb)
            else:
                await cb.answer("Session expired. Please start again.", show_alert=True)
        except Exception:
            await cb.answer("Audio selector error.", show_alert=True)
        return

    await cb.answer()
