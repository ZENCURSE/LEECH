# NXTL Downloaders

This directory contains separate, self-contained downloader modules.
Each file handles a specific protocol or service family, making it easy
to update or fix specific downloaders without touching the rest of the bot.

---

## Files

### `http_downloader.py`
**Protocol:** HTTP / HTTPS  
**Use case:** Direct file downloads over HTTP/HTTPS with streaming and progress.  
**Key function:** `http_download(url, dest_dir, task_id, msg) → str`

---

### `ytdlp_downloader.py`
**Protocol:** yt-dlp (YouTube, M3U8, 1000+ sites)  
**Use case:** Video/audio downloads from YouTube and streaming platforms.  
**Key function:** `ytdlp_download(url, dest_dir, task_id, msg, uid) → str`  
**Config:** Supports per-user cookie files from `users_db`.

---

### `aria2_downloader.py`
**Protocol:** Aria2 JSON-RPC (Torrent / Magnet / HTTP)  
**Use case:** Torrent and magnet downloads via aria2c daemon.  
**Key functions:**
- `torrent_download(src, dest_dir, task_id, msg, is_magnet, existing_gid) → list[str]`
- `torrent_get_files(gid) → list[dict]`
- `torrent_set_selected(gid, indices)`
- `torrent_pause(gid)` / `torrent_resume(gid)` / `torrent_remove(gid)`  
**Config:** `ARIA2_HOST`, `ARIA2_PORT`, `ARIA2_SECRET` in `config.py`

---

### `mega_downloader.py`
**Protocol:** Mega.nz SDK  
**Use case:** File and folder downloads from Mega.nz.  
**Source:** Ported from NEO-WZML with NXTL adapter wrapper.  
**Key function:** `mega_download(link, dest_dir, listener) → None`  
**Config:** `MEGA_EMAIL`, `MEGA_PASSWORD`, `MEGA_ENABLED` in `config.py`

---

### `jd_downloader.py`
**Protocol:** Multi-host direct link resolver  
**Use case:** Downloads from 100+ file sharing sites (MediaFire, GoFile, etc.)  
**Key function:** `jdleech_download(url, dest_dir, task_id, msg) → str`  
**Depends on:** `direct_link_generator.py`

---

### `telegram_downloader.py`
**Protocol:** Telegram MTProto  
**Use case:** Downloads media files directly from Telegram messages.  
**Key function:** `telegram_download(message, dest_path, task_id, msg, client, user_client) → str`

---

### `direct_link_generator.py`
**Protocol:** Unified resolver  
**Use case:** Resolves file-host URLs to direct download links.  
**Source:** Wraps NEO-WZML's extractor + NXTL's original resolver.  
**Key function:** `generate_direct_link(url) → str | tuple | dict`

### `direct_link_generator_neo.py`
**Source:** NEO-WZML (unmodified)  
**Purpose:** Comprehensive direct link extractor for 100+ hosts.

---

## Adding a New Downloader

1. Create `bot/downloaders/my_new_downloader.py`
2. Implement your async download function
3. Add it to `bot/downloaders/__init__.py`
4. Call it from `bot/handlers/download.py` based on URL pattern

## Updating a Downloader

Each file is self-contained. To fix a specific host:
- Edit only `direct_link_generator_neo.py` for file-host extractors
- Edit `ytdlp_downloader.py` for yt-dlp options
- Edit `aria2_downloader.py` for torrent/aria2 behavior
- Edit `mega_downloader.py` for Mega.nz behavior
