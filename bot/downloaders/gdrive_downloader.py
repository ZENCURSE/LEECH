"""
Google Drive Downloader — NXTL
Handles both single files and folders (recursive) from Google Drive.
Uses the Drive v3 API with a service account (token.pickle / credentials.json)
or falls back to yt-dlp for publicly shared files if no credentials are set up.

Supported URL forms:
  https://drive.google.com/file/d/<ID>/view
  https://drive.google.com/open?id=<ID>
  https://drive.google.com/drive/folders/<ID>
  https://drive.google.com/uc?id=<ID>&export=download
  https://drive.usercontent.google.com/download?id=<ID>
"""
import asyncio
import io
import os
import re
import time
from urllib.parse import parse_qs, urlparse

import aiofiles
import aiohttp
import config
from bot import LOGGER
from bot.core import task_manager as tm
from bot.utils.progress import build_progress_card, safe_edit, task_kb

# ── Google Drive MIME types ───────────────────────────────────
FOLDER_MIME  = "application/vnd.google-apps.folder"
GDOCS_MIMES  = {
    "application/vnd.google-apps.document":     ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":  ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",       ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}

CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB resumable chunks


# ── ID extraction ─────────────────────────────────────────────

def extract_gdrive_id(url: str) -> str | None:
    """Extract the file/folder ID from any Google Drive URL form."""
    patterns = [
        r"/file/d/([-\w]+)",
        r"/folders/([-\w]+)",
        r"[?&]id=([-\w]+)",
        r"/d/([-\w]+)",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    # drive.usercontent.google.com/download?id=...
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "id" in qs:
        return qs["id"][0]
    return None


def is_gdrive_folder(url: str) -> bool:
    return "folders" in url


# ── Build Drive service ───────────────────────────────────────

def _build_service():
    """Return an authenticated Drive v3 service, or None if not configured."""
    try:
        import pickle
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists("token.pickle"):
            with open("token.pickle", "rb") as f:
                creds = pickle.load(f)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if creds and creds.valid:
            return build("drive", "v3", credentials=creds)
    except Exception as e:
        LOGGER.warning(f"[GDrive] Could not build service: {e}")
    return None


# ── Main entry point ──────────────────────────────────────────

async def gdrive_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
    uid: int = 0,
) -> str:
    """
    Download a Google Drive file or folder.
    Returns path to the downloaded file or folder.
    """
    os.makedirs(dest_dir, exist_ok=True)
    file_id = extract_gdrive_id(url)
    if not file_id:
        raise ValueError(f"Could not extract Google Drive ID from URL: {url}")

    LOGGER.info(f"[GDrive] Downloading ID={file_id}")
    await safe_edit(msg, _card(task_id, "🔍 Fetching file info from Google Drive…"))

    service = await asyncio.get_event_loop().run_in_executor(None, _build_service)

    if service:
        return await _download_with_api(service, file_id, dest_dir, task_id, msg, uid)
    else:
        # No credentials — use yt-dlp for public files, or gdown for files/folders
        LOGGER.info("[GDrive] No credentials — using gdown fallback")
        return await _download_with_gdown(url, file_id, dest_dir, task_id, msg, uid)


# ── API-based download (authenticated) ───────────────────────

async def _download_with_api(service, file_id, dest_dir, task_id, msg, uid):
    loop = asyncio.get_event_loop()

    meta = await loop.run_in_executor(
        None,
        lambda: service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size",
            supportsTeamDrives=True,
        ).execute()
    )

    name     = meta.get("name", f"gdrive_{file_id}")
    mimetype = meta.get("mimeType", "")
    size     = int(meta.get("size", 0))

    tm.update_progress(task_id, name=name, done=0, total=size, status="downloading")

    if mimetype == FOLDER_MIME:
        folder_path = os.path.join(dest_dir, name)
        os.makedirs(folder_path, exist_ok=True)
        await safe_edit(msg, _card(task_id, f"📁 Downloading folder: {name}"))
        await _download_folder_api(service, file_id, folder_path, task_id, msg, uid)
        return folder_path

    # Google Docs → export
    if mimetype in GDOCS_MIMES:
        export_mime, ext = GDOCS_MIMES[mimetype]
        out_path = os.path.join(dest_dir, name + ext)
        request  = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        out_path = os.path.join(dest_dir, name)
        request  = service.files().get_media(fileId=file_id, supportsTeamDrives=True)

    await safe_edit(msg, _card(task_id, f"⬇️ Downloading: {name}"))
    await loop.run_in_executor(
        None, lambda: _stream_to_file(request, out_path, size, task_id, name)
    )
    return out_path


def _stream_to_file(request, out_path, total, task_id, name):
    """Blocking: stream MediaIoBaseDownload to file."""
    from googleapiclient.http import MediaIoBaseDownload
    with open(out_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=CHUNK_SIZE)
        done = False
        while not done:
            status, done = downloader.next_chunk(num_retries=5)
            if status:
                tm.update_progress(
                    task_id,
                    name=name,
                    done=status.resumable_progress,
                    total=status.total_size or total,
                    status="downloading",
                )


async def _download_folder_api(service, folder_id, local_path, task_id, msg, uid):
    """Recursively download a Drive folder via API."""
    loop = asyncio.get_event_loop()
    page_token = None
    files = []
    while True:
        resp = await loop.run_in_executor(
            None,
            lambda pt=page_token: service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken,files(id,name,mimeType,size)",
                supportsTeamDrives=True,
                includeTeamDriveItems=True,
                pageSize=200,
                pageToken=pt,
            ).execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    for item in files:
        item_id   = item["id"]
        item_name = item["name"]
        mime      = item["mimeType"]
        size      = int(item.get("size", 0))

        if mime == FOLDER_MIME:
            sub = os.path.join(local_path, item_name)
            os.makedirs(sub, exist_ok=True)
            await _download_folder_api(service, item_id, sub, task_id, msg, uid)
        else:
            await safe_edit(msg, _card(task_id, f"⬇️ {item_name}"))
            tm.update_progress(task_id, name=item_name, done=0, total=size, status="downloading")
            if mime in GDOCS_MIMES:
                export_mime, ext = GDOCS_MIMES[mime]
                request = service.files().export_media(fileId=item_id, mimeType=export_mime)
                out_path = os.path.join(local_path, item_name + ext)
            else:
                request  = service.files().get_media(fileId=item_id, supportsTeamDrives=True)
                out_path = os.path.join(local_path, item_name)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda r=request, p=out_path, s=size, n=item_name:
                    _stream_to_file(r, p, s, task_id, n)
            )


# ── gdown fallback (no credentials) ──────────────────────────

async def _download_with_gdown(url, file_id, dest_dir, task_id, msg, uid):
    """Use gdown for public GDrive files/folders (no auth needed)."""
    try:
        import gdown
    except ImportError:
        raise RuntimeError(
            "Google Drive download requires 'gdown'. "
            "Add it to requirements.txt: gdown>=5.0"
        )

    await safe_edit(msg, _card(task_id, "⬇️ Downloading from Google Drive (public)…"))
    tm.update_progress(task_id, name=f"gdrive_{file_id}", done=0, total=0, status="downloading")

    loop = asyncio.get_event_loop()

    if is_gdrive_folder(url):
        out_path = os.path.join(dest_dir, f"gdrive_{file_id}")
        os.makedirs(out_path, exist_ok=True)
        await loop.run_in_executor(
            None,
            lambda: gdown.download_folder(
                id=file_id,
                output=out_path,
                quiet=False,
                use_cookies=False,
            )
        )
    else:
        # Try direct uc URL first (works for most public files)
        direct_url = f"https://drive.google.com/uc?id={file_id}&export=download"
        out_path = os.path.join(dest_dir, f"gdrive_{file_id}")
        result = await loop.run_in_executor(
            None,
            lambda: gdown.download(direct_url, output=out_path, quiet=False, fuzzy=True)
        )
        if not result:
            raise RuntimeError("gdown could not download the file — it may be private or require login.")
        out_path = result

    if not os.path.exists(out_path):
        raise FileNotFoundError(f"gdown finished but nothing was saved to {out_path}")

    LOGGER.info(f"[GDrive] ✅ Downloaded to {out_path}")
    return out_path


# ── Progress card ─────────────────────────────────────────────

def _card(tid: str, step: str) -> str:
    wm = getattr(config, "WATERMARK", "@NXT_HUB")
    return (
        f"╔═「 📂 <b>GOOGLE DRIVE</b> 」\n"
        f"║\n"
        f"║  ➤ {step}\n"
        f"║  ➤ <b>Task</b> : <code>#{tid}</code>\n"
        f"╚══════════════════════\n"
        f"  <i>{wm}</i>"
    )
