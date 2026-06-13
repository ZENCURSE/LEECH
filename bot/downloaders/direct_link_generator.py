"""
Direct Link Generator — NXTL
Unified resolver that wraps:
  1. NEO-WZML's comprehensive extractor (100+ hosts)
  2. NXTL's original direct_links fallback

Supported hosts include: MediaFire, GoFile, PixelDrain, BuzzHeavier,
TeraBox, 1Fichier, OneDrive, Yandex, GitHub, Streamtape, DoodStream,
FileLions, StreamWish, WeTransfer, KrakenFiles, Instagram, and many more.

Usage:
    from bot.downloaders.direct_link_generator import generate_direct_link
    result = generate_direct_link(url)
    # Returns: str (direct URL), tuple (url, header), or dict (folder contents)
"""

from bot.utils.direct_links import get_direct_link as _nxtl_direct


def generate_direct_link(url: str):
    """
    Resolve a file-host URL to a direct download link.
    Tries NEO-WZML extractor first, falls back to NXTL's resolver.

    Returns:
        str   — direct download URL
        tuple — (direct_url, header_string)
        dict  — {title, total_size, contents: [{url, filename, path}], header?}

    Raises:
        RuntimeError if the link cannot be resolved by any extractor.
    """
    # Try the comprehensive NEO-WZML extractor first
    try:
        from bot.downloaders.direct_link_generator_neo import direct_link_generator
        from bot.helper.ext_utils.exceptions import DirectDownloadLinkException
        result = direct_link_generator(url)
        if result:
            return result
    except Exception as neo_err:
        neo_msg = str(neo_err)
        # Fall through to NXTL resolver
        if "No Direct link function found" not in neo_msg:
            # It was a real error from a known handler — propagate it
            raise RuntimeError(neo_msg) from neo_err

    # Fallback: NXTL's original resolver
    try:
        result = _nxtl_direct(url)
        if result:
            return result
    except Exception as nxtl_err:
        raise RuntimeError(
            f"Could not resolve direct link for {url[:80]}: {nxtl_err}"
        ) from nxtl_err

    raise RuntimeError(f"No direct link extractor matched: {url[:80]}")
