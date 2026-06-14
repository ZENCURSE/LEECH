# Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import create_subprocess_exec, create_task, sleep
from asyncio.subprocess import PIPE
from contextlib import suppress
from os import path as ospath, walk
from time import time

from aiofiles import open as aiopen
from aiofiles.os import remove, path as aiopath
from natsort import natsorted

from bot import LOGGER
import config

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts")


class MergeVideos:
    """Merge all video files in a folder into a single MKV using ffmpeg concat."""

    def __init__(self):
        self._processed_bytes = 0
        self._start_time      = 0
        self.error            = ""
        self._cancelled       = False

    @property
    def processed_bytes(self) -> int:
        return self._processed_bytes

    def cancel(self):
        self._cancelled = True

    async def _track_progress(self, output_path: str, proc):
        self._start_time = time()
        while proc.returncode is None and not self._cancelled:
            try:
                self._processed_bytes = ospath.getsize(output_path)
            except OSError:
                pass
            await sleep(1)
        with suppress(OSError):
            self._processed_bytes = ospath.getsize(output_path)

    def _escape_concat_path(self, f_path: str) -> str:
        return f_path.replace("\\", "\\\\").replace("'", "'\\''")

    async def _available_output_path(self, dirpath: str, name: str) -> str:
        output = ospath.join(dirpath, f"{name}_merged.mkv")
        if not await aiopath.exists(output):
            return output
        count = 1
        while True:
            output = ospath.join(dirpath, f"{name}_merged_{count}.mkv")
            if not await aiopath.exists(output):
                return output
            count += 1

    async def merge(self, dl_path: str, task_id: str = "") -> str | None:
        """
        Merge all videos found under dl_path into one MKV.
        Returns output path on success, None on failure.
        """
        dirpath  = dl_path if await aiopath.isdir(dl_path) else ospath.dirname(dl_path)
        name, _  = ospath.splitext(ospath.basename(dl_path))
        output   = await self._available_output_path(dirpath, name)

        all_files = [
            ospath.join(root, f)
            for root, _, files in walk(dirpath)
            for f in files
        ]
        videos = natsorted([
            fp for fp in all_files
            if fp != output
            and fp.lower().endswith(VIDEO_EXTS)
            and "\n" not in fp and "\r" not in fp
        ])

        if not videos:
            LOGGER.warning(f"[MergeVideos] No video files found in {dirpath}")
            return None

        concat_path = ospath.join(config.DOWNLOAD_DIR, f"_concat_{task_id or 'tmp'}.txt")
        try:
            async with aiopen(concat_path, "w", encoding="utf-8") as f:
                for v in videos:
                    await f.write(f"file '{self._escape_concat_path(v)}'\n")

            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_path,
                "-c", "copy",
                output,
            ]
            proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
            progress_task = create_task(self._track_progress(output, proc))
            _, stderr = await proc.communicate()
            with suppress(Exception):
                await progress_task

            if proc.returncode != 0:
                self.error = f"ffmpeg merge failed: {stderr.decode().strip()}"
                LOGGER.error(f"[MergeVideos] {self.error}")
                return None

            for v in videos:
                with suppress(Exception):
                    await remove(v)

            LOGGER.info(f"[MergeVideos] Merged {len(videos)} files → {output}")
            return output

        except Exception as e:
            self.error = f"Merge failed: {e}"
            LOGGER.error(f"[MergeVideos] {self.error}")
            return None
        finally:
            with suppress(Exception):
                await remove(concat_path)
