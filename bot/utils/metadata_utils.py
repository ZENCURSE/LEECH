# Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML)

from os.path import basename, splitext
from re import compile as re_compile

try:
    from pycountry import languages as _pycountry_langs
    _HAS_PYCOUNTRY = True
except ImportError:
    _HAS_PYCOUNTRY = False

from bot.utils.media_utils import get_streams


class MetadataProcessor:
    _year_pattern   = re_compile(r"\b(19|20)\d{2}\b")
    _sanitize_pattern = re_compile(r'[<>:"/\\?*]')

    def __init__(self):
        self.vars = {}
        self.audio_streams    = []
        self.subtitle_streams = []

    @staticmethod
    def convert_lang_code(lang_code: str) -> str:
        if not lang_code or lang_code in {"unknown", "und", "none"}:
            return lang_code
        if not _HAS_PYCOUNTRY:
            return lang_code
        try:
            lang = (
                _pycountry_langs.get(alpha_2=lang_code.lower()) if len(lang_code) == 2
                else _pycountry_langs.get(alpha_3=lang_code.lower()) if len(lang_code) == 3
                else None
            )
            return lang.name if lang else lang_code
        except Exception:
            return lang_code

    async def extract_file_vars(self, file_path: str):
        fname = basename(file_path)
        bname, ext = splitext(fname)
        self.vars = {
            "filename":  fname,
            "basename":  bname,
            "extension": ext.lstrip("."),
            "audiolang": "unknown",
            "sublang":   "none",
        }
        self.audio_streams, self.subtitle_streams = [], []
        try:
            for s in await get_streams(file_path) or []:
                ctype = s.get("codec_type", "").lower()
                slang = s.get("tags", {}).get("language", "unknown")
                full_lang = self.convert_lang_code(slang)
                entry = {
                    "index":         s.get("index", 0),
                    "language":      slang,
                    "full_language": full_lang,
                }
                if ctype == "audio":
                    self.audio_streams.append(entry)
                    if self.vars["audiolang"] == "unknown" and slang != "und":
                        self.vars["audiolang"] = full_lang
                elif ctype == "subtitle":
                    self.subtitle_streams.append(entry)
                    if self.vars["sublang"] == "none" and slang != "und":
                        self.vars["sublang"] = full_lang
        except Exception:
            pass
        m = self._year_pattern.findall(bname)
        if m:
            self.vars["year"] = m[-1]

    @staticmethod
    def parse_string(metadata_str: str) -> dict:
        if not metadata_str or not isinstance(metadata_str, str):
            return {}
        parts, current, i = [], "", 0
        while i < len(metadata_str):
            if metadata_str[i] == "\\" and i + 1 < len(metadata_str) and metadata_str[i+1] == "|":
                current += "|"; i += 2
            elif metadata_str[i] == "|":
                parts.append(current); current = ""; i += 1
            else:
                current += metadata_str[i]; i += 1
        if current:
            parts.append(current)
        return dict(p.split("=", 1) if "=" in p else (p, "") for p in parts)

    def sanitize(self, value) -> str:
        return self._sanitize_pattern.sub("_", str(value))[:100]

    def apply_vars_to_stream(self, metadata_dict, stream_lang=None, full_lang=None, stream_type="audio") -> dict:
        if not isinstance(metadata_dict, dict):
            return {}
        vars_with_stream = self.vars.copy()
        if stream_lang and stream_lang != "unknown":
            key = "audiolang" if stream_type == "audio" else "sublang"
            vars_with_stream[key] = full_lang or self.convert_lang_code(stream_lang)
        return {
            self.sanitize(k): (str(v).format(**vars_with_stream) if isinstance(v, str) else str(v))
            for k, v in metadata_dict.items()
        }

    def apply_vars(self, metadata_dict) -> dict:
        return self.apply_vars_to_stream(metadata_dict)

    def get_audio_metadata(self, audio_metadata_dict) -> list:
        return [
            {"index": s["index"], "metadata": self.apply_vars_to_stream(audio_metadata_dict, s["language"], s["full_language"], "audio")}
            for s in self.audio_streams
        ]

    def get_subtitle_metadata(self, subtitle_metadata_dict) -> list:
        return [
            {"index": s["index"], "metadata": self.apply_vars_to_stream(subtitle_metadata_dict, s["language"], s["full_language"], "subtitle")}
            for s in self.subtitle_streams
        ]

    async def process_all(self, video_metadata_dict, audio_metadata_dict, subtitle_metadata_dict, file_path) -> dict:
        await self.extract_file_vars(file_path)
        return {
            "video":            self.apply_vars(video_metadata_dict) if video_metadata_dict else {},
            "audio_streams":    self.get_audio_metadata(audio_metadata_dict) if audio_metadata_dict else [],
            "subtitle_streams": self.get_subtitle_metadata(subtitle_metadata_dict) if subtitle_metadata_dict else [],
            "global": {},
        }

    async def process(self, metadata_dict, file_path) -> dict:
        await self.extract_file_vars(file_path)
        return self.apply_vars(metadata_dict)
