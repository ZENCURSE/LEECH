"""
enc_db.py — Encoding settings database, uses NXTL's MongoDB connection.

Stores encoding prefs in a separate 'enc_users' collection.
"""
import datetime
import motor.motor_asyncio
import config

_DEFAULTS = dict(
    extensions="MKV",
    hevc=False,
    aspect=False,
    cabac=False,
    reframe="pass",
    tune=True,
    frame="source",
    audio="aac",
    sample="source",
    bitrate="source",
    bits=False,
    channels="source",
    preset="s",       # slow — better compression, avoids file size inflation
    metadata=True,
    hardsub=False,
    watermark=False,
    subtitles=True,
    resolution="OG",
    upload_as_doc=False,
    crf=22,
)


class EncDB:
    def __init__(self):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGO_URI)
        self.col = self._client[config.MONGO_DB]["enc_users"]

    def _new_user(self, uid: int) -> dict:
        return {"id": uid, "join_date": datetime.date.today().isoformat(), **_DEFAULTS}

    async def add_user(self, uid: int):
        if not await self.col.find_one({"id": int(uid)}):
            await self.col.insert_one(self._new_user(int(uid)))

    async def _get(self, uid: int) -> dict:
        user = await self.col.find_one({"id": int(uid)})
        if not user:
            await self.add_user(uid)
            user = await self.col.find_one({"id": int(uid)})
        return user or {}

    async def _set(self, uid: int, **kwargs):
        await self.col.update_one({"id": int(uid)}, {"$set": kwargs}, upsert=True)

    async def delete_user(self, uid: int):
        await self.col.delete_many({"id": int(uid)})

    # ── Getters / Setters ──────────────────────────────────────
    async def get_extensions(self, uid):  return (await self._get(uid)).get("extensions", "MKV")
    async def set_extensions(self, uid, v): await self._set(uid, extensions=v)

    async def get_hevc(self, uid):        return (await self._get(uid)).get("hevc", False)
    async def set_hevc(self, uid, v):     await self._set(uid, hevc=v)

    async def get_aspect(self, uid):      return (await self._get(uid)).get("aspect", False)
    async def set_aspect(self, uid, v):   await self._set(uid, aspect=v)

    async def get_cabac(self, uid):       return (await self._get(uid)).get("cabac", False)
    async def set_cabac(self, uid, v):    await self._set(uid, cabac=v)

    async def get_reframe(self, uid):     return (await self._get(uid)).get("reframe", "pass")
    async def set_reframe(self, uid, v):  await self._set(uid, reframe=v)

    async def get_tune(self, uid):        return (await self._get(uid)).get("tune", True)
    async def set_tune(self, uid, v):     await self._set(uid, tune=v)

    async def get_frame(self, uid):       return (await self._get(uid)).get("frame", "source")
    async def set_frame(self, uid, v):    await self._set(uid, frame=v)

    async def get_audio(self, uid):       return (await self._get(uid)).get("audio", "aac")
    async def set_audio(self, uid, v):    await self._set(uid, audio=v)

    async def get_samplerate(self, uid):  return (await self._get(uid)).get("sample", "source")
    async def set_samplerate(self, uid, v): await self._set(uid, sample=v)

    async def get_bitrate(self, uid):     return (await self._get(uid)).get("bitrate", "source")
    async def set_bitrate(self, uid, v):  await self._set(uid, bitrate=v)

    async def get_bits(self, uid):        return (await self._get(uid)).get("bits", False)
    async def set_bits(self, uid, v):     await self._set(uid, bits=v)

    async def get_channels(self, uid):    return (await self._get(uid)).get("channels", "source")
    async def set_channels(self, uid, v): await self._set(uid, channels=v)


    async def get_preset(self, uid):      return (await self._get(uid)).get("preset", "s")
    async def set_preset(self, uid, v):   await self._set(uid, preset=v)

    async def get_metadata_w(self, uid):  return (await self._get(uid)).get("metadata", False)
    async def set_metadata_w(self, uid, v): await self._set(uid, metadata=v)

    async def get_hardsub(self, uid):     return (await self._get(uid)).get("hardsub", False)
    async def set_hardsub(self, uid, v):  await self._set(uid, hardsub=v)

    async def get_watermark(self, uid):   return (await self._get(uid)).get("watermark", False)
    async def set_watermark(self, uid, v): await self._set(uid, watermark=v)

    async def get_subtitles(self, uid):   return (await self._get(uid)).get("subtitles", True)
    async def set_subtitles(self, uid, v): await self._set(uid, subtitles=v)

    async def get_resolution(self, uid):  return (await self._get(uid)).get("resolution", "OG")
    async def set_resolution(self, uid, v): await self._set(uid, resolution=v)

    async def get_upload_as_doc(self, uid): return (await self._get(uid)).get("upload_as_doc", False)
    async def set_upload_as_doc(self, uid, v): await self._set(uid, upload_as_doc=v)

    async def get_crf(self, uid):         return (await self._get(uid)).get("crf", 22)
    async def set_crf(self, uid, v):      await self._set(uid, crf=int(v))


enc_db = EncDB()
