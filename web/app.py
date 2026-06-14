"""
NXT HUB Web Server — FastAPI
Serves file selection UI for Mega folder and torrent downloads.

Routes:
  GET  /mega-select/{gid}          — file picker page
  POST /mega-select/{gid}/confirm  — confirm selection → start download
  GET  /qbt-select/{gid}           — torrent file picker page
  POST /qbt-select/{gid}/confirm   — confirm torrent file selection
  GET  /health                     — health check
"""
import asyncio
import os

import config
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from web.mega_selection_store import read_state, write_state, update_selected, delete_state
from bot.utils.size_utils import human_size

app       = FastAPI(title="NXT HUB Selector", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

_BASE = config.BASE_URL.rstrip("/") if config.BASE_URL else ""


# ── Health ────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "bot": "NXT HUB"}


# ══════════════════════════════════════════════════════════════
#  MEGA FILE SELECTOR
# ══════════════════════════════════════════════════════════════

@app.get("/mega-select/{gid}", response_class=HTMLResponse)
async def mega_select_page(request: Request, gid: str):
    state = read_state(gid)
    if not state:
        raise HTTPException(404, "Session expired or not found. Send the Mega link again.")

    file_list = state.get("file_list", [])
    files     = [f for f in file_list if not f.get("is_dir")]
    # Strip internal node reference (not JSON-serialisable)
    safe_files = [{k: v for k, v in f.items() if k != "node"} for f in files]

    preselected  = state.get("selected_ids", [])
    total_size   = sum(f.get("size", 0) for f in files)
    confirm_url  = f"{_BASE}/mega-select/{gid}/confirm"

    return templates.TemplateResponse("file_selector.html", {
        "request":     request,
        "gid":         gid,
        "title":       "Mega Folder",
        "source_type": "Mega",
        "files":       safe_files,
        "preselected": preselected,
        "total_files": len(files),
        "total_size":  human_size(total_size),
        "confirm_url": confirm_url,
    })


class SelectionPayload(BaseModel):
    selected_ids: list[str]


@app.post("/mega-select/{gid}/confirm")
async def mega_select_confirm(gid: str, payload: SelectionPayload):
    state = read_state(gid)
    if not state:
        return JSONResponse({"ok": False, "error": "Session expired."})

    selected = payload.selected_ids
    if not selected:
        return JSONResponse({"ok": False, "error": "No files selected."})

    # Persist selection so resume_mega_with_selection can read it
    file_list = state.get("file_list", [])
    if not write_state(gid, file_list, selected):
        return JSONResponse({"ok": False, "error": "Failed to save selection."})

    # Trigger download on the bot's event loop
    try:
        from bot.downloaders.mega_downloader import resume_mega_with_selection
        from bot import bot_loop
        asyncio.run_coroutine_threadsafe(resume_mega_with_selection(gid), bot_loop)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

    return JSONResponse({"ok": True, "count": len(selected)})


# ══════════════════════════════════════════════════════════════
#  TORRENT FILE SELECTOR  (qBittorrent)
# ══════════════════════════════════════════════════════════════

@app.get("/qbt-select/{gid}", response_class=HTMLResponse)
async def qbt_select_page(request: Request, gid: str):
    state = read_state(gid)
    if not state:
        raise HTTPException(404, "Session expired or not found.")

    files        = state.get("file_list", [])
    preselected  = state.get("selected_ids", [])
    total_size   = sum(f.get("size", 0) for f in files)
    confirm_url  = f"{_BASE}/qbt-select/{gid}/confirm"

    return templates.TemplateResponse("file_selector.html", {
        "request":     request,
        "gid":         gid,
        "title":       "Torrent Files",
        "source_type": "Torrent",
        "files":       files,
        "preselected": preselected,
        "total_files": len(files),
        "total_size":  human_size(total_size),
        "confirm_url": confirm_url,
    })


class QbtSelectionPayload(BaseModel):
    selected_ids: list[str]    # file indices as strings


@app.post("/qbt-select/{gid}/confirm")
async def qbt_select_confirm(gid: str, payload: QbtSelectionPayload):
    state = read_state(gid)
    if not state:
        return JSONResponse({"ok": False, "error": "Session expired."})

    selected = payload.selected_ids
    if not selected:
        return JSONResponse({"ok": False, "error": "No files selected."})

    if not update_selected(gid, selected):
        return JSONResponse({"ok": False, "error": "Failed to save selection."})

    # Resume the waiting qbt_download coroutine
    try:
        from bot.downloaders.qbt_downloader import resume_qbt_with_selection
        from bot import bot_loop
        asyncio.run_coroutine_threadsafe(resume_qbt_with_selection(gid), bot_loop)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

    return JSONResponse({"ok": True, "count": len(selected)})


# ══════════════════════════════════════════════════════════════
#  Server launcher
# ══════════════════════════════════════════════════════════════

def run_web_server():
    """Call from bot startup if BASE_URL is configured."""
    import uvicorn
    port = getattr(config, "WEB_PORT", 8080)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
