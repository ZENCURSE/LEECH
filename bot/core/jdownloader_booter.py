"""
JDownloader Booter — NXTL
Connects to MyJDownloader.org using email+password from config.
Uses the myjd library (bundled in /myjd/).
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from myjd.myjdapi import Myjdapi
from bot import LOGGER
import config


class JDownloaderClient:
    def __init__(self):
        self._api        = Myjdapi()
        self.device      = None
        self.is_connected = False
        self.error       = "Not connected yet"

    async def connect(self):
        email    = getattr(config, "JD_EMAIL", "").strip()
        password = getattr(config, "JD_PASS", "") or getattr(config, "JD_PASSWORD", "")
        password = (password or "").strip()
        device   = getattr(config, "JD_DEVICE", "").strip()

        if not email or not password:
            self.error = "JD_EMAIL / JD_PASS not set in config"
            LOGGER.warning(f"[JD] {self.error}")
            return False

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._do_connect, email, password, device)
            self.is_connected = True
            self.error = ""
            LOGGER.info("[JD] Connected to MyJDownloader")
            return True
        except Exception as e:
            self.error = str(e)
            self.is_connected = False
            LOGGER.error(f"[JD] Connection failed: {e}")
            return False

    def _do_connect(self, email, password, device):
        self._api.set_app_key("NXTHUB")
        self._api.connect(email, password)
        self._api.update_devices()
        devices = self._api.list_devices()
        if not devices:
            raise RuntimeError("No JDownloader devices found. Make sure JDownloader is running.")
        # Pick named device or first available
        if device:
            d = next((x for x in devices if x.get("name", "").lower() == device.lower()), None)
            if not d:
                raise RuntimeError(f"Device '{device}' not found. Available: {[x.get('name') for x in devices]}")
        else:
            d = devices[0]
        self.device = self._api.get_device(d["name"])
        LOGGER.info(f"[JD] Using device: {d['name']}")

    async def reconnect(self):
        self.is_connected = False
        self.device = None
        return await self.connect()

    def is_alive(self) -> bool:
        return self.is_connected and self.device is not None


jdownloader = JDownloaderClient()
