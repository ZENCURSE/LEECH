"""
JDownloader Booter — NXTL
Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML).

Boots JDownloader.jar locally. JD connects to MyJDownloader.org
using JD_EMAIL + JD_PASS from config. Bot talks to local API on 127.0.0.1:3128.
"""
import asyncio
import json
import os
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE
from aiofiles import open as aiopen
from aiofiles.os import makedirs, path as aiopath

import config
from bot import LOGGER
from myjd.myjdapi import Myjdapi


JD_DIR = "/JDownloader"
JD_JAR = f"{JD_DIR}/JDownloader.jar"
JD_CFG = f"{JD_DIR}/cfg"


class JDownloader:
    def __init__(self):
        self.device      = None
        self.is_connected = False
        self.error       = "JDownloader not started yet"
        self._api        = None

    async def boot(self):
        email    = getattr(config, "JD_EMAIL", "").strip()
        password = getattr(config, "JD_PASS", "").strip()

        if not email or not password:
            self.error = "JD_EMAIL / JD_PASS not set in config.py"
            LOGGER.warning(f"[JD] {self.error}")
            return

        if not os.path.exists(JD_JAR):
            self.error = f"JDownloader.jar not found at {JD_JAR}"
            LOGGER.error(f"[JD] {self.error}")
            return

        # Write JD config files
        await makedirs(JD_CFG, exist_ok=True)
        jd_settings = {
            "autoconnectenabledv2": True,
            "email": email,
            "password": password,
            "devicename": "NXTHUB",
        }
        remote_settings = {
            "externinterfaceenabled": True,
            "deprecatedapiport": 3128,
            "deprecatedapienabled": True,
            "deprecatedapilocalhostonly": True,
            "jdanywhereapienabled": True,
            "externinterfacelocalhostonly": False,
        }
        async with aiopen(f"{JD_CFG}/org.jdownloader.api.myjdownloader.MyJDownloaderSettings.json", "w") as f:
            await f.write(json.dumps(jd_settings))
        async with aiopen(f"{JD_CFG}/org.jdownloader.api.RemoteAPIConfig.json", "w") as f:
            await f.write(json.dumps(remote_settings))

        LOGGER.info("[JD] Starting JDownloader… (first run may take ~30s for updates)")
        asyncio.create_task(self._run_process())

        # Wait for local API to become available
        for i in range(60):
            await asyncio.sleep(2)
            if await self._try_connect():
                self.is_connected = True
                self.error = ""
                LOGGER.info("[JD] ✅ Connected to local JDownloader API")
                return
            if i % 10 == 0 and i > 0:
                LOGGER.info(f"[JD] Still waiting… ({i*2}s)")

        self.error = "JDownloader did not start within 120s"
        LOGGER.error(f"[JD] {self.error}")

    async def _run_process(self):
        cmd = [
            "cpulimit", "-l", "20", "--",
            "java",
            "-Xms128m", "-Xmx400m",
            "-Dsun.jnu.encoding=UTF-8",
            "-Dfile.encoding=UTF-8",
            "-Djava.awt.headless=true",
            "-jar", JD_JAR,
        ]
        while True:
            proc = await create_subprocess_exec(
                *cmd, stdout=PIPE, stderr=PIPE,
                cwd=JD_DIR,
            )
            await proc.communicate()
            LOGGER.warning("[JD] Process exited — restarting in 5s")
            self.is_connected = False
            await asyncio.sleep(5)
            if await self._try_connect():
                self.is_connected = True

    async def _try_connect(self) -> bool:
        try:
            api = Myjdapi()
            self._api = api
            self.device = api.device
            # Ping the local API
            result = await api.device.system.get_storage_infos()
            return result is not None
        except Exception:
            return False

    def is_alive(self) -> bool:
        return self.is_connected and self.device is not None


jdownloader = JDownloader()
