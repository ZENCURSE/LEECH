"""
extractor.py — Fixed zip and unzip with proper async progress.

Root bugs fixed:
  - Thread callback used asyncio.ensure_future directly on wrong loop →
    replaced with asyncio.run_coroutine_threadsafe(coro, loop) which is
    the correct way to schedule async work from a sync thread.
  - make_zip didn't handle folders → now walks entire directory tree.
  - .7z support added via py7zr (if installed) or system 7z binary.
  - Progress bar uses 🔴⭕ matching rest of bot design.
"""

import os
import asyncio
import zipfile
import tarfile
import time

SUPPORTED_EXTRACT = (".zip", ".rar", ".7z", ".tar", ".gz", ".tar.gz", ".tgz")
SUPPORTED_ZIP     = (".zip",)


# ── Thread-safe async progress edit ──────────────────────────
async def _edit_progress(msg, tid: str, done: int, total: int, fname: str):
    if not msg:
        return
    from pyrogram import enums
    from bot.utils.progress import task_kb
    pct        = int(done / total * 100) if total else 0
    bar_filled = round(12 * done / total) if total else 0
    bar        = "🔴" * bar_filled + "⭕" * (12 - bar_filled)
    short      = (fname[:28] + "…") if len(fname) > 30 else fname
    try:
        await msg.edit_text(
            f"<b>📂 EXTRACTING</b>\n\n"
            f"{bar}  <b>{pct}%</b>\n\n"
            f"📄 <code>{short}</code>\n"
            f"📦 <b>{done}</b> / <b>{total}</b> files\n\n"
            f"🆔 <code>{tid}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=task_kb(tid),
        )
    except Exception:
        pass


async def _edit_zip_progress(msg, tid: str, done: int, total: int, fname: str):
    if not msg:
        return
    from pyrogram import enums
    from bot.utils.progress import task_kb
    pct        = int(done / total * 100) if total else 0
    bar_filled = round(12 * done / total) if total else 0
    bar        = "🔴" * bar_filled + "⭕" * (12 - bar_filled)
    short      = (fname[:28] + "…") if len(fname) > 30 else fname
    try:
        await msg.edit_text(
            f"<b>🗜 ZIPPING</b>\n\n"
            f"{bar}  <b>{pct}%</b>\n\n"
            f"📄 <code>{short}</code>\n"
            f"📦 <b>{done}</b> / <b>{total}</b> files\n\n"
            f"🆔 <code>{tid}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=task_kb(tid),
        )
    except Exception:
        pass


# ── Sync extractors (run in thread pool) ─────────────────────

def _extract_zip(path: str, dest: str, loop, msg, tid: str):
    try:
        with zipfile.ZipFile(path, "r") as z:
            members = z.infolist()
            total   = len(members)
            last    = 0.0
            for i, member in enumerate(members, 1):
                z.extract(member, dest)
                now = time.monotonic()
                if now - last >= 2.0:
                    last = now
                    asyncio.run_coroutine_threadsafe(
                        _edit_progress(msg, tid, i, total, member.filename), loop
                    )
    except zipfile.BadZipFile as e:
        raise ValueError(f"Corrupt or invalid ZIP file: {e}")


def _extract_tar(path: str, dest: str, loop, msg, tid: str):
    try:
        with tarfile.open(path, "r:*") as t:
            members = t.getmembers()
            total   = len(members)
            last    = 0.0
            for i, member in enumerate(members, 1):
                t.extract(member, dest, set_attrs=False)
                now = time.monotonic()
                if now - last >= 2.0:
                    last = now
                    asyncio.run_coroutine_threadsafe(
                        _edit_progress(msg, tid, i, total, member.name), loop
                    )
    except tarfile.TarError as e:
        raise ValueError(f"Corrupt or invalid TAR file: {e}")


def _extract_rar(path: str, dest: str, loop, msg, tid: str):
    try:
        import rarfile
        with rarfile.RarFile(path) as r:
            members = r.infolist()
            total   = len(members)
            last    = 0.0
            for i, member in enumerate(members, 1):
                r.extract(member, dest)
                now = time.monotonic()
                if now - last >= 2.0:
                    last = now
                    asyncio.run_coroutine_threadsafe(
                        _edit_progress(msg, tid, i, total, member.filename), loop
                    )
    except ImportError:
        # Fallback to system unrar
        ret = os.system(f'unrar x -o+ "{path}" "{dest}" > /dev/null 2>&1')
        if ret != 0:
            raise ValueError("unrar failed. Is 'unrar' installed on the system?")


def _extract_7z(path: str, dest: str, loop, msg, tid: str):
    try:
        import py7zr
        with py7zr.SevenZipFile(path, mode="r") as z:
            members = z.getnames()
            total   = len(members)
            # py7zr doesn't support per-file callbacks easily — extract all
            z.extractall(path=dest)
            asyncio.run_coroutine_threadsafe(
                _edit_progress(msg, tid, total, total, "Done"), loop
            )
    except ImportError:
        ret = os.system(f'7z x "{path}" -o"{dest}" -y > /dev/null 2>&1')
        if ret != 0:
            raise ValueError("7z extraction failed. Is '7z' or 'py7zr' installed?")


# ── Sync zipper ───────────────────────────────────────────────

def _make_zip_sync(src_path: str, dest_zip: str, loop, msg, tid: str):
    """
    Zip a file or entire directory tree into dest_zip.
    Shows progress via 🔴⭕ bar.
    """
    # Collect all files to zip
    files_to_zip = []
    if os.path.isfile(src_path):
        files_to_zip = [(src_path, os.path.basename(src_path))]
    elif os.path.isdir(src_path):
        base = os.path.dirname(src_path)
        for root, _, files in os.walk(src_path):
            for f in files:
                abs_p  = os.path.join(root, f)
                arc_p  = os.path.relpath(abs_p, base)
                files_to_zip.append((abs_p, arc_p))

    total = len(files_to_zip)
    if total == 0:
        raise ValueError("Nothing to zip — source is empty.")

    last = 0.0
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for i, (abs_p, arc_p) in enumerate(files_to_zip, 1):
            z.write(abs_p, arc_p)
            now = time.monotonic()
            if now - last >= 2.0:
                last = now
                asyncio.run_coroutine_threadsafe(
                    _edit_zip_progress(msg, tid, i, total,
                                       os.path.basename(abs_p)), loop
                )


# ── Public async API ──────────────────────────────────────────

async def extract(path: str, dest_dir: str,
                  progress_msg=None, task_id: str = "") -> list[str]:
    """
    Extract an archive with live progress.
    Returns list of all extracted file paths.
    Raises ValueError with a clear message on failure.
    """
    from pyrogram import enums
    from bot.utils.progress import task_kb

    if not os.path.isfile(path):
        raise FileNotFoundError(f"Archive not found: {path}")

    os.makedirs(dest_dir, exist_ok=True)
    loop = asyncio.get_event_loop()
    ext  = path.lower()

    # Show initial card immediately
    if progress_msg and task_id:
        try:
            await progress_msg.edit_text(
                f"<b>📂 EXTRACTING</b>\n\n"
                f"{'⭕' * 12}  <b>0%</b>\n\n"
                f"📄 <code>Reading archive…</code>\n\n"
                f"🆔 <code>{task_id}</code>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=task_kb(task_id),
            )
        except Exception:
            pass

    executor = None  # use default ThreadPoolExecutor

    try:
        if ext.endswith(".zip"):
            await asyncio.get_event_loop().run_in_executor(
                executor, _extract_zip, path, dest_dir, loop, progress_msg, task_id
            )
        elif ext.endswith(".rar"):
            await asyncio.get_event_loop().run_in_executor(
                executor, _extract_rar, path, dest_dir, loop, progress_msg, task_id
            )
        elif ext.endswith(".7z"):
            await asyncio.get_event_loop().run_in_executor(
                executor, _extract_7z, path, dest_dir, loop, progress_msg, task_id
            )
        elif any(ext.endswith(e) for e in (".tar", ".tar.gz", ".tgz", ".gz")):
            await asyncio.get_event_loop().run_in_executor(
                executor, _extract_tar, path, dest_dir, loop, progress_msg, task_id
            )
        else:
            raise ValueError(
                f"Unsupported archive format.\n"
                f"Supported: .zip .rar .7z .tar .tar.gz .tgz .gz"
            )
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Extraction failed: {e}") from e

    # Collect extracted files (skip hidden/system files)
    extracted = []
    for root, _, files in os.walk(dest_dir):
        for f in files:
            if not f.startswith("__MACOSX") and not f.startswith("."):
                extracted.append(os.path.join(root, f))

    if not extracted:
        raise ValueError("Archive extracted but no files found inside.")

    # Single completion card
    if progress_msg and task_id:
        try:
            await progress_msg.edit_text(
                f"<b>📂 EXTRACTION COMPLETE</b>\n\n"
                f"{'🔴' * 12}  <b>100%</b>\n\n"
                f"✅ <b>{len(extracted)}</b> file(s) extracted\n"
                f"🆔 <code>{task_id}</code>\n\n"
                f"📤 Uploading now…",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=task_kb(task_id),
            )
        except Exception:
            pass

    return extracted


async def make_zip(src_path: str, dest_zip: str,
                   progress_msg=None, task_id: str = "") -> str:
    """
    Zip a file or directory with live progress.
    Returns path to the created zip file.
    """
    from pyrogram import enums
    from bot.utils.progress import task_kb

    if progress_msg and task_id:
        try:
            await progress_msg.edit_text(
                f"<b>🗜 ZIPPING</b>\n\n"
                f"{'⭕' * 12}  <b>0%</b>\n\n"
                f"📄 <code>Preparing…</code>\n\n"
                f"🆔 <code>{task_id}</code>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=task_kb(task_id),
            )
        except Exception:
            pass

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, _make_zip_sync, src_path, dest_zip, loop, progress_msg, task_id
        )
    except Exception as e:
        raise ValueError(f"Zip failed: {e}") from e

    if not os.path.isfile(dest_zip):
        raise ValueError("Zip file was not created.")

    # Completion card
    if progress_msg and task_id:
        from bot.utils.size_utils import human_size
        size = human_size(os.path.getsize(dest_zip))
        try:
            await progress_msg.edit_text(
                f"<b>🗜 ZIP COMPLETE</b>\n\n"
                f"{'🔴' * 12}  <b>100%</b>\n\n"
                f"✅ <b>{os.path.basename(dest_zip)}</b>\n"
                f"📦 <b>{size}</b>\n"
                f"🆔 <code>{task_id}</code>\n\n"
                f"📤 Uploading now…",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=task_kb(task_id),
            )
        except Exception:
            pass

    return dest_zip
