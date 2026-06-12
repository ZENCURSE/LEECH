"""
Encoding helper — bridges encoding.py with NXTL's uploader.
Supports external subtitle injection for hardsub via /encsub.
"""
import os
import shutil
import logging
import config

log = logging.getLogger("encoding")


async def handle_encode(filepath: str, message, msg, external_sub: str = None, audio_map=None):
    """
    Encode filepath with user's settings, then upload via NXTL uploader.

    external_sub: path to an external .ass/.srt subtitle file.
                  When provided, hardsub is forced ON using this file
                  regardless of the user's hardsub toggle setting.
    """
    uid     = message.from_user.id
    enc_dir = getattr(config, "ENCODE_DIR", config.DOWNLOAD_DIR + "_enc")
    os.makedirs(enc_dir, exist_ok=True)

    from bot.encoding.encoding import encode

    try:
        out = await encode(
            filepath, message, msg,
            audio_map=audio_map,
            external_sub=external_sub,
        )
    except Exception as e:
        log.error(f"Encode failed: {e}")
        try:
            await msg.edit_text(
                f"❌ <b>Encoding failed:</b>\n<code>{e}</code>",
                parse_mode="html",
            )
        except Exception:
            pass
        return

    if not out or not os.path.isfile(out):
        try:
            await msg.edit_text(
                "❌ <b>Encoding failed</b> — no output produced.",
                parse_mode="html",
            )
        except Exception:
            pass
        return

    from bot import uploader_client
    from bot.core.uploader import upload_file
    from bot.core import task_manager as tm

    task_id = tm.create_task(uid, os.path.basename(out))

    try:
        uclient = uploader_client()
        await upload_file(
            uclient, uid, out, task_id, msg, uid,
            origin_msg=message, is_group=False,
        )
    except Exception as e:
        log.error(f"Upload after encode failed: {e}")
        try:
            await msg.edit_text(
                f"❌ <b>Upload failed:</b>\n<code>{e}</code>",
                parse_mode="html",
            )
        except Exception:
            pass
    finally:
        tm.finish_task(task_id)
        for p in (filepath, out, external_sub):
            if not p:
                continue
            try:
                parent = os.path.dirname(p)
                base   = config.DOWNLOAD_DIR.rstrip("/")
                if len(parent) > len(base) and parent.startswith(base):
                    shutil.rmtree(parent, ignore_errors=True)
                elif os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
