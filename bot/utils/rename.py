import re
import os

# ── Site watermarks ───────────────────────────────────────────
_SITES = [
    # Indian piracy sites
    "moviesmod","hdhub4u","vegamovies","extraflix","rogmovies","toonworld4all",
    "filmyzilla","filmywap","filmyhit","bollyflix","bolly4u","bollyshare",
    "khatrimaza","katmoviehd","9xmovies","7starhd","300mbfilms","downloadhub",
    "movierulz","tamilblasters","tamilrockers","isaimini","jiorockers",
    "skymovies","skymovieshd","mp4moviez","4movierulz","worldfree4u",
    "world4ufree","mkvcage","mkvking","mkvcinemas","mkvhub","coolmoviez",
    "fzmovies","besthdmovies","moviescounter","moviescouch","afilmywap",
    "sdmoviespoint","pagalmovies","desiremovies","cinevood","gomovies",
    "hindilinks4u","hdmovieshub","hdmovie99","hdmoviez4u","djpunjab",
    "toxicwap","o2tvseries","desimartini","9xrockers","9xflix","9xmovie",
    "kuttymovies","moviesda","tamilgun","tamilyogi","madrasrockers",
    "mallumv","malayalamrockers","cinemavilla","moviezwap","teluguwap",
    "ibomma","aha","thepiratebay","torrentz2","torrentking",
    "tamilmv","telugurockers","kannadarockers","moviezadda","skysetx",
    "filmy4wap","filmy4web","extramovies","khatrimazafull","123mkv",
    # International
    "1337x","yts","rarbg","eztv","ettv","ganool","netnaija",
    "123movies","fmovies","putlocker","uwatchfree","7movierulz","pahe",
    "openload","streamtape","gdrive","gdflix","hubcloud",
]

# Compiled once — word-boundary aware, case-insensitive
_SITE_RE = re.compile(
    r"(?i)(?<![a-z0-9])(" + "|".join(re.escape(s) for s in _SITES) + r")(?![a-z0-9])"
)

# Matches any domain pattern: (www.)anything.tld
_SITE_TLD_RE = re.compile(
    r"(?i)[\s.\-_\[\](]*(www\.)?[a-z0-9]+"
    r"\.(com|net|org|in|io|co|me|tv|mobi|site|xyz|club|info|cc|vc|pw|ws|to|li|re|nl)"
    r"(?![a-z0-9])"
    r"[\s.\-_\[\])]*"
)

# ── Split point: where the "title" ends and the tags/quality info begins ──
# Everything BEFORE the first match of this is treated as pure title text
# and is never touched beyond separator→space conversion. Everything from
# here onward is the "tags zone", where site-name/tech-tag stripping runs.
# This is what keeps things like "Chand.Mera.Dil" or "23000" intact — the
# aggressive regexes below never even see the title portion of the name.
_SPLIT_RE = re.compile(
    r"(?i)\b("
    r"(?:19|20)\d{2}"                                        # year
    r"|480p|576p|720p|1080p|1080i|2160p|4k|8k|uhd"           # resolution
    r"|bluray|blu-ray|bdrip|web-?dl|webrip|hdtv|hdrip|hdtc|hdcam|dvdrip|dvdscr"  # source
    r"|s\d{1,2}[.\-]?e\d{1,2}(?:[.\-]?e\d{1,2})*"            # S01E01
    r"|s\d{1,2}(?![\d.]\d)"                                   # standalone S01
    r"|\d{1,2}x\d{2}"                                         # 1x01
    r")\b"
)

# ── Season/Episode patterns — protect these ───────────────────
# Matches: S01E01, S01E01E02, S01, E01, 1x01
_SE_RE = re.compile(
    r"(?i)(?<![a-z\d])"
    r"("
    r"S\d{1,2}[.\-]?E\d{1,2}(?:[.\-]?E\d{1,2})*"   # S01E01 / S01E01E02
    r"|S\d{1,2}(?![\d.]\d)"                           # S01 (standalone season)
    r"|E\d{1,2}(?![\d.]\d)"                           # E01 (standalone episode)
    r"|\d{1,2}[xX]\d{2}"                              # 1x01 notation
    r")"
    r"(?![a-z\d])"
)

# ── Audio channel notation — protect AAC2.0, AAC5.1, 7.1, 2.0 etc. ──
# We protect these BEFORE separator replacement so dots aren't eaten
_AUDIO_CH_RE = re.compile(r"(?i)([257][\.]1|2[\.]0)")

# ── Tech tags — STRIP these ───────────────────────────────────
# Everything NOT in this list is KEPT (resolution, codec, source, audio)
_TECH_STRIP_RE = re.compile(
    r"(?i)\b("
    # Junk rip types
    r"dvdrip|dvdscr|dvdmux|r5|scr|screener|workprint|telecine"
    # Old/irrelevant codecs
    r"|xvid|divx|av1|vp9|mpeg2|mpeg4|wmv|rmvb"
    # HDR metadata noise (keep plain HDR/SDR names? strip internal flags)
    r"|dovi|dv(?=[\s.\-_]|$)"
    # Dolby Digital shortcodes (keep the descriptive ones like Atmos, TrueHD)
    r"|dd[\-. ]?[257][\-. ]?[01]|ddp[\-. ]?[257][\-. ]?[01]"
    # Release group junk
    r"|repack|rerip|proper|readnfo|nfofix|retail|internal"
    # Cut/edition noise
    r"|theatrical|directors\.?cut|unrated|limited|extended"
    # Localization flags
    r"|subbed|dubbed|multi|dual[\-. ]?audio|dubbed"
    # Streaming platform tags (not meaningful in filename)
    r"|nf|amzn|amazon|dsnp|hmax|pcok|atvp|cr|hulu|stan|it|zee5|sonyliv|voot"
    # File-size suffixes
    r"|mb|gb"
    r")\b"
)

# ── Casing normalisation map for known tags ───────────────────
_TAG_CASE = {
    "bluray": "BluRay", "blu-ray": "BluRay",
    "bdrip": "BDRip",
    "web-dl": "WEB-DL", "webdl": "WEB-DL",
    "webrip": "WEBRip",
    "hdtv": "HDTV", "hdtc": "HDTC", "hdcam": "HDCAM",
    "hdrip": "HDRip",
    "x264": "x264", "x265": "x265",
    "h264": "H.264", "h.264": "H.264",
    "h265": "H.265", "h.265": "H.265",
    "hevc": "HEVC",
    "10bit": "10bit", "10-bit": "10bit",
    "aac": "AAC", "ac3": "AC3", "eac3": "EAC3",
    "dts": "DTS", "dts-hd": "DTS-HD",
    "truehd": "TrueHD", "flac": "FLAC",
    "atmos": "Atmos", "opus": "Opus",
    "2160p": "2160p", "1080p": "1080p", "1080i": "1080i",
    "720p": "720p", "720i": "720i",
    "480p": "480p", "360p": "360p", "240p": "240p",
    "4k": "4K", "8k": "8K", "uhd": "UHD",
}

# ── Separators safe to replace with space ─────────────────────
# Hyphen handled carefully: only replace when NOT inside a known tech tag
_SEP_RE = re.compile(r"[._/\\()\[\]{}|@#*!~`+]")


def _normalise_tags(text: str) -> str:
    """Apply canonical casing to known tech tags."""
    def _replace(m):
        return _TAG_CASE.get(m.group(0).lower(), m.group(0))
    pattern = re.compile(
        r"(?i)\b(" + "|".join(re.escape(k) for k in _TAG_CASE) + r")\b"
    )
    return pattern.sub(_replace, text)


def _strip_leading_site(text: str) -> str:
    """Strip a website name/domain/bracket tag ONLY if it's the very first
    thing in the string — e.g. '[TamilMV] Movie...', 'www.site.com - Movie',
    'ExtraFlix Movie...'. Never touches a site-shaped word anywhere else,
    which is what used to eat real title words/numbers that merely
    contained a matching substring."""
    prev = None
    while text != prev:
        prev = text
        # bracket/paren tag at the very start: "[TamilMV] ", "(site.com) "
        text = re.sub(r"^\s*[\[\(][^\]\)]{1,40}[\]\)]\s*[-–—:]?\s*", "", text)
        # domain at the very start: "www.site.com ", "site.com - "
        text = re.sub(
            r"(?i)^\s*(www\.)?[a-z0-9]+\.(com|net|org|in|io|co|me|tv|mobi|site|"
            r"xyz|club|info|cc|vc|pw|ws|to|li|re|nl)(?![a-z0-9])\s*[-–—:]?\s*",
            "", text,
        )
        # known piracy site name as the very first word
        text = re.sub(
            r"(?i)^\s*(" + "|".join(re.escape(s) for s in _SITES) + r")"
            r"(?![a-z0-9])\s*[-–—:]?\s*",
            "", text,
        )
    return text


def clean_name(name: str) -> str:
    """
    Sanitise a media filename with a strict split between the free-text
    TITLE and the TAGS (quality/source/codec/audio) that follow it:

      - TITLE (everything before the year/resolution/source/SxxExx marker):
        left completely as written — the only change is converting
        separators (. _ - etc.) to spaces. A website name is stripped from
        here ONLY if it's a leading prefix (e.g. "[TamilMV] Movie...",
        "www.site.com - Movie..."); a site-shaped word anywhere else in
        the title is never touched, so real title words/numbers can't be
        collaterally eaten (this is what used to turn "Chand.Mera.Dil"
        into "ra Dil", or eat numbers like "23000").
      - TAGS (from that marker onward): site watermarks, tech-junk flags
        (dvdrip, xvid, dubbed …) and trailing release-group tags are
        stripped here, same as before; resolution/codec/source/audio are
        kept and casing-normalised (bluray → BluRay, webdl → WEB-DL …).

    Examples
    --------
    Interstellar.2014.1080p.BluRay.x264-moviesmod.mkv
      → Interstellar 2014 1080p BluRay x264.mkv

    Chand.Mera.Dil.2026.720p.WEB-DL.Hindi.AAC2.0.H.265-ExtraFlix.Pw.mkv
      → Chand Mera Dil 2026 720p WEB-DL Hindi AAC2.0 H.265.mkv

    The.Boys.S03E01.1080p.WEB-DL.x265.AAC5.1.mkv
      → The Boys S03E01 1080p WEB-DL x265 AAC5.1.mkv
    """
    stem, ext = os.path.splitext(name)

    m = _SPLIT_RE.search(stem)
    title_part = stem[: m.start()] if m else stem
    tags_part  = stem[m.start():] if m else ""

    # ── TITLE: strip a leading site tag only, then just de-separator it ──
    title_part = _strip_leading_site(title_part)
    title_part = _SEP_RE.sub(" ", title_part)
    title_part = re.sub(r"(?<!\w)-(?!\w)|(?<=\s)-|-(?=\s)", " ", title_part)
    title_part = re.sub(r"\s+", " ", title_part).strip()

    # ── TAGS: same stripping pipeline as before, scoped to this zone only ─
    # 1. Protect season/episode tokens
    _se_map: dict[str, str] = {}
    def _protect_se(mm: re.Match) -> str:
        key = f"PLSE{len(_se_map)}PLSE"
        _se_map[key] = re.sub(r"[.\-]", "", mm.group(0).upper())
        return key
    tags_part = _SE_RE.sub(_protect_se, tags_part)

    # 2. Protect audio channel notation (AAC5.1, 7.1, 2.0 …)
    _audio_map: dict[str, str] = {}
    def _protect_audio(mm: re.Match) -> str:
        key = f"PLAU{len(_audio_map)}PLAU"
        _audio_map[key] = mm.group(0)
        return key
    tags_part = _AUDIO_CH_RE.sub(_protect_audio, tags_part)

    # 3. Strip domain watermarks (www.site.com / site.com)
    tags_part = _SITE_TLD_RE.sub(" ", tags_part)

    # 4. Strip known piracy site names (2 passes for adjacent tokens)
    for _ in range(2):
        tags_part = _SITE_RE.sub(" ", tags_part)

    # 5. Replace separators (dots, underscores, brackets …) with space
    tags_part = _SEP_RE.sub(" ", tags_part)

    # 6. Replace hyphens that are NOT inside compound tech tags with space
    tags_part = re.sub(r"(?<!\w)-(?!\w)|(?<=\s)-|-(?=\s)", " ", tags_part)

    # 7. Strip junk tech tags
    tags_part = _TECH_STRIP_RE.sub(" ", tags_part)

    # 8. Strip common scene release group tags (e.g. -YIFY, -FGT, -RARBG at end)
    tags_part = re.sub(r"(?i)\s*-\s*[A-Z0-9]{2,10}$", "", tags_part)
    tags_part = re.sub(
        r"(?i)\s+[A-Z]{2,8}$",
        lambda mm: "" if mm.group().strip().isupper() else mm.group(),
        tags_part,
    )

    # 9. Restore protected tokens
    for key, val in _audio_map.items():
        tags_part = tags_part.replace(key, val)
    for key, val in _se_map.items():
        tags_part = tags_part.replace(key, val)

    tags_part = re.sub(r"\s+", " ", tags_part).strip()
    tags_part = _normalise_tags(tags_part)

    stem = f"{title_part} {tags_part}".strip() if tags_part else title_part
    stem = re.sub(r"\s+", " ", stem).strip()

    return stem + ext


def apply_prefix_suffix(name: str, prefix: str = "", suffix: str = "") -> str:
    """Attach a prefix and/or suffix to the stem of a filename."""
    stem, ext = os.path.splitext(name)
    prefix = (prefix or "").strip()
    suffix = (suffix or "").strip()
    parts  = [p for p in [prefix, stem, suffix] if p]
    return " ".join(parts) + ext


def smart_rename(name: str, prefix: str = "", suffix: str = "") -> str:
    """Clean filename then apply optional prefix/suffix."""
    return apply_prefix_suffix(clean_name(name), prefix, suffix)


def parse_title_year(name: str) -> tuple[str, str | None]:
    """
    Extract (title, year) from a filename for TMDB/IMDB lookups.

    Unlike clean_name() (which intentionally KEEPS language tags like
    "Hindi"/"Tamil" since they're useful in the displayed filename),
    this function strips them — TMDB search returns zero results for
    queries like "Hindi Dubbed Jawan" or "Jawan Hindi", since the
    language word isn't part of the actual title.

    Returns
    -------
    (title, year)  — year is None if not found.

    Examples
    --------
    "Interstellar 2014 1080p BluRay.mkv"        → ("Interstellar", "2014")
    "The Boys S03E01 1080p WEB-DL.mkv"          → ("The Boys", None)
    "Hindi Dubbed Jawan 2023 1080p WEBRip.mkv"  → ("Jawan", "2023")
    "[TamilMV] Jawan 2023 1080p.mkv"            → ("Jawan", "2023")
    "Jawan (2023) 1080p WEBRip.mkv"             → ("Jawan", "2023")
    """
    stem = os.path.splitext(clean_name(name))[0]

    # clean_name() strips bracket-wrapped/domain site tags BEFORE converting
    # separators to spaces, but if a site tag was bracket-wrapped with mixed
    # casing not in the known _SITES list (e.g. "[TamilMV]"), or was part of
    # a domain-like prefix ("www.Tamilrockers.cm -"), fragments can survive
    # as plain words after the dots/brackets become spaces. Re-run the site
    # regexes against the cleaned stem to catch these leftovers.
    stem = _SITE_TLD_RE.sub(" ", stem)
    for _ in range(2):
        stem = _SITE_RE.sub(" ", stem)
    # Generic leftover bracket-tag words clean_name() didn't recognise —
    # short ALL-CAPS-ish tokens or known piracy-tag shapes at the start,
    # plus short 2-3 letter typo-TLD fragments left behind anywhere
    # (e.g. "tamilrockers.cm" → site name stripped, ".cm" fragment remains)
    stem = re.sub(r"(?i)^\s*(www|cm|cc|co|in|io|net|org)\b\s*", "", stem)
    stem = re.sub(r"(?i)\b(cm|cc|vc|pw|ws|gg|sh|la)\b\s*", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()

    # Strip leading bracket/paren tags clean_name() may have left as plain
    # text adjacent to brackets it removed, e.g. residual "[TamilMV] Jawan"
    # if separator replacement ran before site matching in edge cases.
    stem = re.sub(r"^\s*\[[^\]]{0,40}\]\s*", "", stem)
    stem = re.sub(r"^\s*\([^)]{0,40}\)\s*", "", stem)

    year_match = re.search(r"\b((?:19|20)\d{2})\b", stem)
    if year_match:
        year  = year_match.group(1)
        title = stem[: year_match.start()].strip()
    else:
        year  = None
        title = stem.strip()

    # Strip everything from first SE marker onwards in title
    title = re.sub(r"\s+S\d{1,2}E?\d{0,2}.*$", "", title, flags=re.I).strip()
    title = re.sub(
        r"(?i)\s+(1080p|720p|480p|2160p|4K|UHD|BluRay|WEB-DL|WEBRip|HEVC|x264|x265).*$",
        "", title
    ).strip()

    # Strip language tags — these break TMDB search and can appear
    # as a prefix ("Hindi Dubbed Jawan") or suffix ("Jawan Hindi").
    # Common audio-track and dub labels seen in pirated release names.
    _lang_words = (
        r"hindi|tamil|telugu|malayalam|kannada|bengali|punjabi|marathi|gujarati"
        r"|english|korean|japanese|chinese|spanish|french|german|russian"
        r"|dubbed|dual\s*audio|multi\s*audio|multi\b"
    )
    title = re.sub(rf"(?i)^\s*(?:{_lang_words})\s+", "", title).strip()
    title = re.sub(rf"(?i)\s+(?:{_lang_words})\s*$", "", title).strip()
    # Run twice — handles "Hindi Dubbed Jawan" (two leading tokens)
    title = re.sub(rf"(?i)^\s*(?:{_lang_words})\s+", "", title).strip()

    # Strip any remaining stray brackets/parens/site-leftover punctuation
    # at the edges (e.g. "Jawan (" left over when year was "(2023)")
    title = re.sub(r"[\[\]{}()]+", "", title).strip()
    title = re.sub(r"[\-_:|]+$", "", title).strip()
    title = re.sub(r"^[\-_:|]+", "", title).strip()
    title = re.sub(r"\s+", " ", title).strip()

    return title or "Untitled", year


def batch_rename(
    folder: str,
    extensions: tuple[str, ...] = (".mkv", ".mp4", ".avi", ".mov", ".ts"),
    prefix: str = "",
    suffix: str = "",
    dry_run: bool = True,
) -> list[tuple[str, str]]:
    """
    Rename all media files in *folder* using smart_rename.

    Parameters
    ----------
    folder     : Path to directory containing media files.
    extensions : Only files with these extensions are processed.
    prefix     : Optional prefix added to every cleaned name.
    suffix     : Optional suffix added to every cleaned name.
    dry_run    : If True (default), print changes without renaming.
                 Set to False to actually rename files on disk.

    Returns
    -------
    List of (old_name, new_name) pairs for every file that would change.
    """
    folder = os.path.abspath(folder)
    changes: list[tuple[str, str]] = []

    for fname in sorted(os.listdir(folder)):
        if not fname.lower().endswith(extensions):
            continue
        new_name = smart_rename(fname, prefix=prefix, suffix=suffix)
        if new_name == fname:
            continue
        changes.append((fname, new_name))
        if dry_run:
            print(f"  [DRY] {fname!r}\n     → {new_name!r}")
        else:
            src = os.path.join(folder, fname)
            dst = os.path.join(folder, new_name)
            if not os.path.exists(dst):
                os.rename(src, dst)
                print(f"  [OK] {fname!r} → {new_name!r}")
            else:
                print(f"  [SKIP] target exists: {new_name!r}")

    if not changes:
        print("  No changes needed.")
    return changes
