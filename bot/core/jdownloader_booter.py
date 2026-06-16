"""
JDownloader Booter — NXTL
Boots JDownloader.jar locally. JD connects to MyJDownloader.org
using JD_EMAIL + JD_PASS from config. Bot talks to local API on 127.0.0.1:3128.
Only needs email + password — no device selection.
"""
import asyncio
import json
import os
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE
from aiofiles import open as aiopen
from aiofiles.os import makedirs

import config
from bot import LOGGER
from myjd.myjdapi import MyJdApi       # correct class name


JD_DIR = "/JDownloader"
JD_JAR = f"{JD_DIR}/JDownloader.jar"
JD_CFG = f"{JD_DIR}/cfg"


class JDownloader:
    def __init__(self):
        self.device       = None
        self.is_connected = False
        self.error        = "JDownloader not started yet"
        self._api         = None

    async def boot(self):
        email    = getattr(config, "JD_EMAIL", "").strip()
        password = getattr(config, "JD_PASS",  "").strip()

        if not email or not password:
            self.error = "JD_EMAIL / JD_PASS not set in config.py"
            LOGGER.warning(f"[JD] {self.error}")
            return

        if not os.path.exists(JD_JAR):
            self.error = f"JDownloader not available (jar not found)"
            LOGGER.info("[JD] JDownloader.jar not present — JD features disabled. Bot running normally.")
            return

        # Write JD config files so it auto-connects to MyJDownloader.org
        await makedirs(JD_CFG, exist_ok=True)

        myjd_settings = {
            "autoconnectenabledv2": True,
            "email":      email,
            "password":   password,
            "devicename": "NXTHUB",
        }
        remote_settings = {
            "externinterfaceenabled":       True,
            "deprecatedapiport":            3128,
            "deprecatedapienabled":         True,
            "deprecatedapilocalhostonly":   True,
            "jdanywhereapienabled":         True,
            "externinterfacelocalhostonly": False,
        }

        async with aiopen(
            f"{JD_CFG}/org.jdownloader.api.myjdownloader.MyJDownloaderSettings.json", "w"
        ) as f:
            await f.write(json.dumps(myjd_settings))

        async with aiopen(
            f"{JD_CFG}/org.jdownloader.api.RemoteAPIConfig.json", "w"
        ) as f:
            await f.write(json.dumps(remote_settings))

        LOGGER.info("[JD] Starting JDownloader.jar… (first boot may take ~30s)")
        asyncio.create_task(self._run_process())

        # Wait up to 120s for local API to respond
        for i in range(60):
            await asyncio.sleep(2)
            if await self._try_connect():
                self.is_connected = True
                self.error        = ""
                LOGGER.info("[JD] ✅ Local API ready on 127.0.0.1:3128")
                return
            if i > 0 and i % 10 == 0:
                LOGGER.info(f"[JD] Still waiting… ({i * 2}s elapsed)")

        self.error = "JDownloader did not start within 120s"
        LOGGER.error(f"[JD] {self.error}")

    async def _run_process(self):
        """Keep JDownloader.jar running — restart if it exits."""
        cmd = [
            "java",
            "-Xms64m", "-Xmx384m",
            "-Dsun.jnu.encoding=UTF-8",
            "-Dfile.encoding=UTF-8",
            "-Djava.awt.headless=true",
            "-jar", JD_JAR,
        ]
        while True:
            try:
                proc = await create_subprocess_exec(
                    *cmd, stdout=PIPE, stderr=PIPE, cwd=JD_DIR
                )
                await proc.communicate()
            except Exception as e:
                LOGGER.error(f"[JD] Process error: {e}")
            LOGGER.warning("[JD] Process exited — restarting in 10s")
            self.is_connected = False
            await asyncio.sleep(10)
            if await self._try_connect():
                self.is_connected = True

    async def _try_connect(self) -> bool:
        """Ping the local JDownloader API."""
        try:
            api = MyJdApi()
            self._api   = api
            self.device = api.device
            result = await api.device.jd.version()
            return result is not None
        except Exception:
            return False

    def is_alive(self) -> bool:
        return self.is_connected and self.device is not None


jdownloader = JDownloader()
