"""
helper.py — Encode → Upload bridge  (full rewrite)
====================================================
Calls encoding.encode(), then hands the output to the NXTL uploader.
Cleans up all temp files regardless of outcome.
"""
import os
import shutil
import logging

import config

log = logging.getLogger("encoding")


async def handle_encode(
    filepath:     str,
    message,
    msg,
    external_sub: str  | None = None,
    audio_map:    list | None = None,
    tid:          str         = "",
):
    """
    Encode `filepath` then upload the result.
    `tid` is passed through so the progress card shows a cancel button.
    All temp directories are removed in the finally block.
    """
    from bot.encoding.encoding import encode
    from bot.core import task_manager as tm

    uid = message.from_user.id

    # Generate a task ID if none was supplied
    if not tid:
        tid = tm.create_task(uid, os.path.basename(filepath))

    out = None
    try:
        out = await encode(
            filepath, message, msg,
            audio_map=audio_map,
            external_sub=external_sub,
            tid=tid,
        )
    except Exception as e:
        log.error(f"[Helper] Encode failed: {e}")
        try:
            await msg.edit_text(
                f"❌ <b>Encoding failed:</b>\n<code>{e}</code>",
                parse_mode="html",
            )
        except Exception:
            pass
        return
    finally:
        # Always remove the source work dir if it's under DOWNLOAD_DIR
        _try_remove(filepath)

    if not out or not os.path.isfile(out):
        try:
            await msg.edit_text(
                "❌ <b>Encoding failed</b> — no output produced.",
                parse_mode="html",
            )
        except Exception:
            pass
        tm.finish_task(tid)
        return

    # ── Upload ────────────────────────────────────────────────
    from bot import uploader_client
    from bot.core.uploader import upload_file

    try:
        uclient = uploader_client()
        await upload_file(
            uclient, uid, out, tid, msg, uid,
            origin_msg=message,
            is_group=False,
            progress_msg=msg,
        )
    except Exception as e:
        log.error(f"[Helper] Upload failed: {e}")
        try:
            await msg.edit_text(
                f"❌ <b>Upload failed:</b>\n<code>{e}</code>",
                parse_mode="html",
            )
        except Exception:
            pass
    finally:
        tm.finish_task(tid)
        _try_remove(out)
        if external_sub:
            _try_remove(external_sub)


def _try_remove(path: str | None):
    """Remove a file or its parent work dir if it's inside DOWNLOAD_DIR."""
    if not path:
        return
    try:
        base = config.DOWNLOAD_DIR.rstrip("/")
        parent = os.path.dirname(os.path.abspath(path))
        # Remove the whole work dir if it's one level inside DOWNLOAD_DIR
        if (
            len(parent) > len(base)
            and parent.startswith(base)
            and parent != base
        ):
            shutil.rmtree(parent, ignore_errors=True)
        elif os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        log.debug(f"_try_remove({path}): {e}")
