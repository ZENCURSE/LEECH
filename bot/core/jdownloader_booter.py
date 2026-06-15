"""
JDownloader Booter — NXTL
Connects to MyJDownloader.org using email+password from config.
Uses the official 'myjdapi' PyPI package.

Only JD_EMAIL and JD_PASS are required. JD_DEVICE is optional — if left
blank, the first device linked to the MyJDownloader account is used
automatically (same behaviour as other WZML-based bots).
"""
import asyncio

try:
    import myjdapi
except ImportError:  # pragma: no cover - dependency missing
    myjdapi = None

from bot import LOGGER
import config


class JDownloaderClient:
    def __init__(self):
        self._api         = None
        self.device       = None
        self.is_connected = False
        self.error        = "Not connected yet"

    async def connect(self):
        if myjdapi is None:
            self.error = "myjdapi package is not installed (pip install myjdapi)"
            LOGGER.warning(f"[JD] {self.error}")
            return False

        email    = (getattr(config, "JD_EMAIL", "") or "").strip()
        password = (getattr(config, "JD_PASS", "") or getattr(config, "JD_PASSWORD", "") or "").strip()
        device   = (getattr(config, "JD_DEVICE", "") or "").strip()

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
        api = myjdapi.Myjdapi()
        api.set_app_key("NXTHUB")
        api.connect(email, password)
        api.update_devices()
        devices = api.list_devices()

        if not devices:
            raise RuntimeError(
                "No JDownloader devices found on this MyJDownloader account. "
                "Make sure JDownloader is running and signed in with the same "
                "email/password."
            )

        if device:
            d = next((x for x in devices if x.get("name", "").lower() == device.lower()), None)
            if not d:
                raise RuntimeError(
                    f"Device '{device}' not found. Available: {[x.get('name') for x in devices]}"
                )
        else:
            d = devices[0]

        self._api   = api
        self.device = api.get_device(d["name"])
        LOGGER.info(f"[JD] Using device: {d['name']}")

    async def reconnect(self):
        self.is_connected = False
        self.device = None
        return await self.connect()

    def is_alive(self) -> bool:
        return self.is_connected and self.device is not None


jdownloader = JDownloaderClient()
