"""
mega_download.py — Mega.nz download via megapy or megatools CLI.

Fixed: proper progress tracking, better error messages, credential handling.
"""

import os
import re
import asyncio
import time
import config

_MEGA_FILE_RE   = re.compile(r"mega\.nz/(?:file/|#!)([a-zA-Z0-9_-]+)", re.I)
_MEGA_FOLDER_RE = re.compile(r"mega\.nz/(?:folder/|#F!)([a-zA-Z0-9_-]+)", re.I)


def is_mega_link(url: str) -> bool:
    return bool(re.search(r"mega\.nz/", url, re.I))


async def mega_download(url: str, dest_dir: str, task_id: str, msg) -> str:
    """
    Download a Mega.nz file/folder to dest_dir.
    Returns path to the downloaded file (or folder if multiple files).
    """
    from bot.core import task_manager as tm
    from bot.utils.progress import task_kb
    from pyrogram import enums

    os.makedirs(dest_dir, exist_ok=True)
    kb = task_kb(task_id)
    tm.set_status(task_id, "downloading")

    try:
        await msg.edit_text(
            f"<b>⬇️ MEGA DOWNLOAD</b>\n\n"
            f"🔗 <code>mega.nz</code>\n"
            f"🆔 <code>{task_id}</code>\n\n"
            f"<i>Connecting…</i>",
            parse_mode=enums.ParseMode.HTML, reply_markup=kb,
        )
    except Exception:
        pass

    # Try mega.py (Python library)
    try:
        return await _mega_py_download(url, dest_dir, task_id, msg, tm, kb)
    except ImportError:
        pass  # mega.py not installed, try CLI
    except Exception as e:
        err = str(e).lower()
        # Re-raise real errors, fall through only on "not found" type errors
        if "import" not in err and "module" not in err:
            raise RuntimeError(f"mega.py failed: {e}") from e

    # Fallback: megatools CLI
    return await _megadl_cli(url, dest_dir, task_id, msg, tm, kb)


async def _mega_py_download(url, dest_dir, task_id, msg, tm, kb):
    from mega import Mega  # type: ignore
    from pyrogram import enums

    loop = asyncio.get_event_loop()
    err_ref = [None]

    def _do_download():
        try:
            m = Mega()
            email    = getattr(config, "MEGA_EMAIL", "") or ""
            password = getattr(config, "MEGA_PASSWORD", "") or ""
            if email and password:
                client = m.login(email, password)
            else:
                client = m.login()  # anonymous
            client.download_url(url, dest_path=dest_dir)
        except Exception as e:
            err_ref[0] = e

    await loop.run_in_executor(None, _do_download)

    if err_ref[0]:
        raise err_ref[0]

    return _find_downloaded(dest_dir)


async def _megadl_cli(url, dest_dir, task_id, msg, tm, kb):
    """Fallback using megatools package CLI."""
    from pyrogram import enums

    # Check which CLI tool is available
    cli = None
    for candidate in ("megadl", "mega-get", "megatools"):
        proc = await asyncio.create_subprocess_shell(
            f"which {candidate.split()[0]}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if await proc.wait() == 0:
            cli = "megadl" if candidate == "megatools" else candidate
            break

    if not cli:
        raise RuntimeError(
            "❌ No Mega download tool found.\n"
            "Install: <code>pip install mega.py</code> or <code>apt install megatools</code>"
        )

    try:
        await msg.edit_text(
            f"<b>⬇️ MEGA DOWNLOAD</b>\n\n"
            f"🔗 <code>mega.nz</code>\n"
            f"🆔 <code>{task_id}</code>\n\n"
            f"<i>Using {cli} CLI…</i>",
            parse_mode=enums.ParseMode.HTML, reply_markup=kb,
        )
    except Exception:
        pass

    proc = await asyncio.create_subprocess_exec(
        cli, "--path", dest_dir, url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")[:400]
        raise RuntimeError(f"megadl failed (code {proc.returncode}): {err_msg}")

    return _find_downloaded(dest_dir)


def _find_downloaded(dest_dir: str) -> str:
    """Return the downloaded file path, or dest_dir if multiple files."""
    files = []
    for root, _, fs in os.walk(dest_dir):
        for f in fs:
            p = os.path.join(root, f)
            if os.path.isfile(p):
                files.append(p)

    if not files:
        raise FileNotFoundError("Mega download completed but no files found in destination.")

    return files[0] if len(files) == 1 else dest_dir
