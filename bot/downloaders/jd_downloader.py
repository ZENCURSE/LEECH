"""
JD Leech / Multi-Host Downloader — NXTL
Resolves direct download links from 100+ file hosts using built-in
extractors. No external API credentials required for most sites.

Supported: MediaFire, PixelDrain, BuzzHeavier, GoFile, TeraBox, 1Fichier,
KrakenFiles, WeTransfer, OneDrive, Yandex, Streamtape, DoodStream,
FileLions/StreamWish, DevUploads, GitHub Releases, Instagram, and more.

Uses NEO-WZML's comprehensive direct_link_generator under the hood.
"""
import asyncio

from bot.downloaders.http_downloader import http_download


async def jdleech_download(url: str, dest_dir: str, task_id: str, msg) -> str:
    """
    Resolve a file-host URL to a direct link, then download it via HTTP.
    Returns the local path of the downloaded file.
    Raises RuntimeError if the link cannot be resolved.
    """
    from bot.downloaders.direct_link_generator import generate_direct_link

    loop = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(None, generate_direct_link, url)
    except Exception as e:
        raise RuntimeError(f"JDLeech: Could not resolve link — {e}") from e

    # Folder result: dict with list of files
    if isinstance(result, dict) and "contents" in result:
        paths = []
        for item in result["contents"]:
            item_url = item.get("url") or item.get("direct_link", "")
            if not item_url:
                continue
            try:
                p = await http_download(item_url, dest_dir, task_id, msg)
                paths.append(p)
            except Exception:
                pass
        if paths:
            return paths[0] if len(paths) == 1 else dest_dir
        raise RuntimeError("JDLeech: Folder resolved but no files downloaded.")

    # Tuple: (direct_url, header)
    if isinstance(result, tuple):
        direct_url, header = result
        return await http_download(direct_url, dest_dir, task_id, msg)

    # Single direct URL string
    if isinstance(result, str) and result.startswith("http"):
        return await http_download(result, dest_dir, task_id, msg)

    raise RuntimeError(f"JDLeech: Could not resolve — {url[:80]}")
