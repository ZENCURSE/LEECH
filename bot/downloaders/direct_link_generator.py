"""
Direct Link Generator — NXTL
Wraps NEO-WZML's comprehensive extractor (100+ hosts).

Supported hosts include: MediaFire, GoFile, PixelDrain, BuzzHeavier,
TeraBox, 1Fichier, OneDrive, Yandex, GitHub, Streamtape, DoodStream,
FileLions, StreamWish, WeTransfer, KrakenFiles, Instagram, and many more.

Usage:
    from bot.downloaders.direct_link_generator import generate_direct_link
    result = generate_direct_link(url)
    # Returns: str (direct URL), tuple (url, header), or dict (folder contents)
"""


def generate_direct_link(url: str):
    """
    Resolve a file-host URL to a direct download link via the NEO-WZML extractor.

    Returns:
        str   — direct download URL
        tuple — (direct_url, header_string)
        dict  — {title, total_size, contents: [{url, filename, path}], header?}

    Raises:
        RuntimeError if the link cannot be resolved.
    """
    from bot.downloaders.direct_link_generator_neo import direct_link_generator, DirectDownloadLinkException
    try:
        result = direct_link_generator(url)
        if result:
            return result
    except Exception as neo_err:
        raise RuntimeError(str(neo_err)) from neo_err

    raise RuntimeError(f"No direct link extractor matched: {url[:80]}")
