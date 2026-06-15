"""
Mega.nz Downloader — NXTL
Pure-Python implementation using requests + PyCryptodome.
No SDK bindings, no mega.py package dependency.

Supports:
  - File links:   https://mega.nz/file/ID#KEY
  - Folder links: https://mega.nz/folder/ID#KEY  (downloads all files)
  - Legacy #! and #F! formats

Error "Url key missing" is now impossible — we parse the key ourselves.
"""
import os
import re
import json
import struct
import base64
import asyncio
import time
import math
import random
import aiohttp
import aiofiles

from Crypto.Cipher import AES
from Crypto.Util import Counter

import config
from bot import LOGGER


# ── Mega API endpoint ─────────────────────────────────────────
MEGA_API = "https://g.api.mega.co.nz/cs"
CHUNK_SZ = 2 * 1024 * 1024   # 2 MB download chunks

UPDATE_SEC = 4


# ════════════════════════════════════════════════════════════
#  Crypto helpers (Mega uses a custom XOR + AES-CTR scheme)
# ════════════════════════════════════════════════════════════

def _b64d(s: str) -> bytes:
    """Mega base64url → bytes (no-padding variant)."""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode().replace("+", "-").replace("/", "_").rstrip("=")

def _chunks_to_key(chunks: list[int]) -> list[int]:
    """Convert list of 32-bit ints into Mega's 128-bit key."""
    return [
        chunks[0] ^ chunks[4],
        chunks[1] ^ chunks[5],
        chunks[2] ^ chunks[6],
        chunks[3] ^ chunks[7],
    ]

def _int32_from_bytes(b: bytes, offset: int) -> int:
    return struct.unpack(">I", b[offset:offset+4])[0]

def _bytes_to_int32_list(b: bytes) -> list[int]:
    return [_int32_from_bytes(b, i) for i in range(0, len(b), 4)]

def _int32_list_to_bytes(lst: list[int]) -> bytes:
    return b"".join(struct.pack(">I", x & 0xFFFFFFFF) for x in lst)

def _aes_decrypt_ecb(key: bytes, data: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.decrypt(data)

def _aes_cbc_decrypt(key: bytes, data: bytes, iv: bytes = b"\x00" * 16) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.decrypt(data)

def _aes_ctr_decrypt(key: bytes, iv_int: int, data: bytes) -> bytes:
    ctr = Counter.new(128, initial_value=iv_int)
    cipher = AES.new(key, AES.MODE_CTR, counter=ctr)
    return cipher.decrypt(data)

def _decrypt_attr(encrypted_attr: bytes, key: bytes) -> dict | None:
    """Decrypt Mega node attributes JSON."""
    try:
        decrypted = _aes_cbc_decrypt(key, encrypted_attr)
        # Strip trailing nulls, find MEGA{ ... }
        text = decrypted.decode("utf-8", errors="ignore").rstrip("\x00")
        m = re.search(r"MEGA(\{.*?\})", text)
        if m:
            return json.loads(m.group(1))
    except Exception as e:
        LOGGER.error(f"[Mega] attr decrypt failed: {e}")
    return None

def _parse_url(url: str) -> tuple[str, str, str]:
    """
    Returns (mode, node_id, key_b64).
    mode = 'file' | 'folder'
    Raises ValueError if URL can't be parsed or key is missing.
    """
    url = url.strip()

    # New format: /file/ID#KEY  or  /folder/ID#KEY
    m = re.search(
        r"mega\.n[ze]/(?:embed#)?(?:#!?)?(file|folder)/([a-zA-Z0-9_-]+)#([a-zA-Z0-9_-]+)",
        url, re.I,
    )
    if m:
        return m.group(1).lower(), m.group(2), m.group(3)

    # Legacy file: /#!ID!KEY  or  /#!ID#KEY
    m = re.search(r"mega\.n[ze]/#!([a-zA-Z0-9_-]+)[!#]([a-zA-Z0-9_-]+)", url, re.I)
    if m:
        return "file", m.group(1), m.group(2)

    # Legacy folder: /#F!ID!KEY
    m = re.search(r"mega\.n[ze]/#F!([a-zA-Z0-9_-]+)[!#]([a-zA-Z0-9_-]+)", url, re.I)
    if m:
        return "folder", m.group(1), m.group(2)

    # No key found
    m = re.search(r"mega\.n[ze]/(?:file|folder)/([a-zA-Z0-9_-]+)", url, re.I)
    if m:
        raise ValueError(
            "Mega link is missing its decryption key (the part after #). "
            "Copy the full link from Mega."
        )

    raise ValueError(f"Cannot parse Mega URL: {url}")


# ════════════════════════════════════════════════════════════
#  Mega API helpers
# ════════════════════════════════════════════════════════════

def _api_req(payload: list, session: str = None, folder_id: str = None) -> dict | list:
    import requests as _req
    params = {"id": random.randint(1, 0xFFFFFFFF)}
    if session:
        params["sid"] = session
    if folder_id:
        params["n"] = folder_id
    r = _req.post(MEGA_API, params=params, json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    if isinstance(result, list):
        if len(result) == 1 and isinstance(result[0], int) and result[0] < 0:
            raise RuntimeError(f"Mega API error code: {result[0]}")
        return result[0] if len(result) == 1 else result
    return result


def _login(email: str, password: str) -> str:
    """Log in and return session string."""
    from hashlib import pbkdf2_hmac
    import requests as _req

    # Derive login key
    pw_bytes = password.encode("utf-8")
    # Mega password key derivation (legacy)
    key_bytes = pw_bytes
    pkey = [0x93C467E3, 0x7DB0C7A4, 0xD1BE3F81, 0x0152CB56]
    for _ in range(65536):
        for j in range(0, len(key_bytes), 4):
            chunk = key_bytes[j:j+4].ljust(4, b"\x00")
            pkey[j//4 % 4] ^= _int32_from_bytes(chunk + b"\x00"*(4-len(chunk[:4])), 0)
        pkey = _bytes_to_int32_list(
            _aes_decrypt_ecb(_int32_list_to_bytes(pkey[:2]*2), _int32_list_to_bytes(pkey))
        )

    pw_key = _int32_list_to_bytes(pkey)
    uh = _b64e(_aes_decrypt_ecb(pw_key, email.lower().encode("utf-8").ljust(16, b"\x00")[:16]))

    resp = _api_req([{"a": "us", "user": email, "uh": uh}])
    if isinstance(resp, int):
        raise RuntimeError(f"Login failed (code {resp})")
    return resp.get("k", ""), resp.get("csid", "")


# ════════════════════════════════════════════════════════════
#  File download
# ════════════════════════════════════════════════════════════

async def _download_file(
    url: str, dest_path: str, file_key_raw: bytes,
    task_id: str, msg, update_fn,
) -> str:
    """Stream-decrypt and save a Mega file."""
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))

            done = 0
            last_upd = time.monotonic()

            # Derive AES-CTR key and IV from file key
            k = _bytes_to_int32_list(file_key_raw)
            key    = _int32_list_to_bytes(_chunks_to_key(k))
            iv_int = (k[4] << 96) | (k[5] << 64)

            ctr = Counter.new(128, initial_value=iv_int)
            cipher = AES.new(key, AES.MODE_CTR, counter=ctr)

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            async with aiofiles.open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SZ):
                    decrypted = await asyncio.get_event_loop().run_in_executor(
                        None, cipher.decrypt, chunk
                    )
                    await f.write(decrypted)
                    done += len(chunk)

                    now = time.monotonic()
                    if now - last_upd >= UPDATE_SEC:
                        last_upd = now
                        await update_fn(done, total)

    return dest_path


# ════════════════════════════════════════════════════════════
#  Public entry point
# ════════════════════════════════════════════════════════════

async def mega_download(
    url: str,
    dest_dir: str,
    task_id: str,
    msg,
) -> str:
    """
    Download a Mega.nz file or folder.
    Returns path of downloaded file/folder.
    Raises RuntimeError on failure.
    """
    import time as _time
    from bot.core import task_manager as tm
    from bot.utils.progress import build_progress_card, safe_edit
    from bot.utils.size_utils import human_size, human_speed

    started = _time.monotonic()
    last_speed = [0.0]
    last_done  = [0]
    last_t     = [_time.monotonic()]

    async def _update(done: int, total: int):
        now = _time.monotonic()
        dt  = now - last_t[0]
        if dt > 0:
            speed = (done - last_done[0]) / dt
            last_speed[0] = speed
            last_done[0]  = done
            last_t[0]     = now
        else:
            speed = last_speed[0]

        pct = (done / total * 100) if total else 0
        eta = ((total - done) / speed) if speed > 0 and total > done else 0
        tm.update_progress(task_id, done=done, total=total,
                           speed=speed, eta=eta, status="downloading")
        await safe_edit(
            msg,
            build_progress_card(
                "downloading", _name[0], pct,
                done=done, total=total,
                speed=speed, eta=eta,
                elapsed=_time.monotonic() - started,
                tid=task_id,
            ),
        )

    _name = ["downloading…"]
    os.makedirs(dest_dir, exist_ok=True)

    loop = asyncio.get_event_loop()

    try:
        # ── 1. Parse URL ──────────────────────────────────────
        mode, node_id, key_b64 = await loop.run_in_executor(None, _parse_url, url)
        file_key_raw = _b64d(key_b64)

        email    = getattr(config, "MEGA_EMAIL", "").strip()
        password = getattr(config, "MEGA_PASSWORD", "").strip()

        # ── 2. Optionally login ───────────────────────────────
        session_id = None
        if email and password:
            try:
                _, session_id = await loop.run_in_executor(None, _login, email, password)
                LOGGER.info("[Mega] Logged in successfully")
            except Exception as e:
                LOGGER.warning(f"[Mega] Login failed (will try anon): {e}")

        if mode == "file":
            # ── 3a. Single file ───────────────────────────────
            def _get_file_info():
                return _api_req([{"a": "g", "g": 1, "p": node_id}], session=session_id)

            info = await loop.run_in_executor(None, _get_file_info)
            if isinstance(info, int):
                raise RuntimeError(f"Mega file request failed (code {info})")

            dl_url   = info["g"]
            attr_raw = _b64d(info["at"])
            k_raw    = _b64d(info["k"].split(":", 1)[-1]) if ":" in info.get("k", "") else file_key_raw

            # decrypt key
            k_list = _bytes_to_int32_list(k_raw)
            if len(k_list) >= 8:
                key_raw = _int32_list_to_bytes(_chunks_to_key(k_list))
            else:
                key_raw = k_raw

            # decrypt attributes to get filename
            attr = _decrypt_attr(attr_raw, key_raw[:16])
            fname = (attr or {}).get("n", f"mega_{node_id}")
            _name[0] = fname
            dest = os.path.join(dest_dir, fname)

            tm.set_status(task_id, "downloading")
            LOGGER.info(f"[Mega] Downloading file: {fname}")
            await _download_file(dl_url, dest, _b64d(key_b64), task_id, msg, _update)
            return dest

        else:
            # ── 3b. Folder ────────────────────────────────────
            def _get_folder_nodes():
                return _api_req([{"a": "f", "c": 1, "ca": 1}],
                                session=session_id, folder_id=node_id)

            folder_data = await loop.run_in_executor(None, _get_folder_nodes)
            if isinstance(folder_data, int):
                raise RuntimeError(f"Mega folder request failed (code {folder_data})")

            nodes = folder_data.get("f", [])
            root  = next((n for n in nodes if n.get("p") == ""), None) or nodes[0]
            root_handle = root["h"]

            # Decrypt master folder key
            folder_key_raw = _b64d(key_b64)
            fk_list = _bytes_to_int32_list(folder_key_raw)
            if len(fk_list) >= 8:
                master_key = _int32_list_to_bytes(_chunks_to_key(fk_list))
            else:
                master_key = folder_key_raw[:16]

            # Resolve folder name from root node attrs
            root_attr_raw = _b64d(root.get("at", ""))
            root_attr = _decrypt_attr(root_attr_raw, master_key)
            folder_name = (root_attr or {}).get("n", f"mega_folder_{node_id}")
            _name[0] = folder_name
            folder_dest = os.path.join(dest_dir, folder_name)
            os.makedirs(folder_dest, exist_ok=True)

            # Collect file nodes
            files = [n for n in nodes if n.get("t", 1) == 0]  # t=0 means file
            LOGGER.info(f"[Mega] Folder '{folder_name}': {len(files)} files")

            for i, node in enumerate(files, 1):
                if tm.is_cancelled(task_id):
                    raise asyncio.CancelledError

                # Decrypt this node's key using master key
                node_k_str = node.get("k", "")
                node_k_b64 = node_k_str.split(":", 1)[-1] if ":" in node_k_str else node_k_str
                node_k_raw = _aes_decrypt_ecb(master_key, _b64d(node_k_b64))
                nk_list    = _bytes_to_int32_list(node_k_raw)
                if len(nk_list) >= 8:
                    node_key = _int32_list_to_bytes(_chunks_to_key(nk_list))
                else:
                    node_key = node_k_raw[:16]

                # Decrypt attrs → filename
                attr_raw = _b64d(node.get("at", ""))
                attr     = _decrypt_attr(attr_raw, node_key)
                fname    = (attr or {}).get("n", f"file_{node['h']}")

                # Get download URL for this node
                def _get_node_url(h=node["h"]):
                    return _api_req([{"a": "g", "g": 1, "n": h}],
                                    session=session_id, folder_id=node_id)

                info = await loop.run_in_executor(None, _get_node_url)
                if isinstance(info, int):
                    LOGGER.warning(f"[Mega] Skipping {fname} (code {info})")
                    continue

                dl_url = info["g"]
                dest   = os.path.join(folder_dest, fname)

                LOGGER.info(f"[Mega] [{i}/{len(files)}] {fname}")
                _name[0] = f"[{i}/{len(files)}] {fname}"
                tm.set_status(task_id, "downloading")

                await _download_file(dl_url, dest, node_k_raw, task_id, msg, _update)

            return folder_dest

    except asyncio.CancelledError:
        raise
    except ValueError as e:
        raise RuntimeError(str(e)) from e
    except Exception as e:
        LOGGER.error(f"[Mega] Download failed: {e}", exc_info=True)
        raise RuntimeError(f"mega failed: {e}") from e
