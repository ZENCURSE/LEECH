"""
users_db.py — MongoDB-backed DB with in-memory cache + flat-file fallback.

Set MONGO_URI in config.py to enable MongoDB.
Leave blank → falls back to data/users.json (dev mode).

Collections (MongoDB):
  users    — per-user settings  { _id: uid, ...settings }
  started  — users who PM-started bot  { _id: uid }
  acl      — { _id: "owners"|"admins", list: [...] }
"""
import os
import config

_MONGO_URI = getattr(config, "MONGO_URI", "") or os.environ.get("MONGO_URI", "")

if _MONGO_URI:
    from motor.motor_asyncio import AsyncIOMotorClient
    _mclient = AsyncIOMotorClient(
        _MONGO_URI,
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
        socketTimeoutMS=10000,
        retryWrites=True,
        tls=True,
    )
    _mdb     = _mclient[getattr(config, "MONGO_DB", "nxthub")]
    _col_u   = _mdb["users"]
    _col_s   = _mdb["started"]
    _col_acl = _mdb["acl"]
    USE_MONGO = True
else:
    USE_MONGO = False

# ── in-memory cache ───────────────────────────────────────────
_owners:   list[int] | None = None
_admins:   list[int] | None = None
_started:  set[int]  | None = None
_settings: dict[int, dict]  = {}

import asyncio, json

# ── flat-file helpers ─────────────────────────────────────────
_DB = "data/users.json"

def _jload():
    if os.path.exists(_DB):
        try:
            with open(_DB) as f: return json.load(f)
        except Exception: pass
    return {"owners": [config.OWNER_ID], "admins": [], "settings": {}, "started": []}

def _jsave(db):
    os.makedirs("data", exist_ok=True)
    with open(_DB, "w") as f: json.dump(db, f, indent=2)

def _def():
    return {"prefix": "", "suffix": "", "thumb_path": None,
            "cookies_path": None, "as_doc": getattr(config, "AS_DOCUMENT", False),
            "rename_regex": "", "caption": "", "dump_channel": ""}

# ── startup init ──────────────────────────────────────────────
async def init_db():
    global _owners, _admins, _started, USE_MONGO
    if USE_MONGO:
        # Retry MongoDB connection up to 3 times before falling back to JSON
        for attempt in range(1, 4):
            try:
                od = await _col_acl.find_one({"_id": "owners"})
                ad = await _col_acl.find_one({"_id": "admins"})
                _owners  = (od or {}).get("list", [config.OWNER_ID])
                _admins  = (ad or {}).get("list", [])
                if not od:
                    await _col_acl.update_one({"_id": "owners"},
                        {"$setOnInsert": {"list": [config.OWNER_ID]}}, upsert=True)
                docs = await _col_s.find({}, {"_id": 1}).to_list(None)
                _started = {d["_id"] for d in docs}
                await _col_u.create_index("_id")
                await _col_s.create_index("_id")
                print("[DB] MongoDB connected.")
                return
            except Exception as e:
                print(f"[DB] MongoDB attempt {attempt}/3 failed: {e}")
                if attempt < 3:
                    await asyncio.sleep(2)
                else:
                    print("[DB] MongoDB unreachable — falling back to flat-file JSON mode.")
                    print("[DB] ⚠️  Fix: Go to MongoDB Atlas → Network Access → Add 0.0.0.0/0")
                    USE_MONGO = False

    # Flat-file fallback
    db = _jload()
    _owners  = db.get("owners", [config.OWNER_ID])
    _admins  = db.get("admins", [])
    _started = set(db.get("started", []))
    print("[DB] Running in flat-file JSON mode (data/users.json).")

def _ensure():
    """Sync fallback if init_db wasn't awaited yet."""
    global _owners, _admins, _started
    if _owners is None:
        db = _jload()
        _owners  = db.get("owners",  [config.OWNER_ID])
        _admins  = db.get("admins",  [])
        _started = set(db.get("started", []))

def _fire(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(coro)
        else:
            loop.run_until_complete(coro)
    except Exception: pass

# ── ACL ───────────────────────────────────────────────────────
def is_owner(uid): _ensure(); return uid in (_owners or [])
def is_admin(uid): _ensure(); return uid in (_admins or []) or uid in (_owners or [])

def add_owner(uid):
    _ensure()
    if uid in _owners: return False
    _owners.append(uid)
    if USE_MONGO: _fire(_col_acl.update_one({"_id":"owners"}, {"$addToSet":{"list":uid}}, upsert=True))
    else:
        db = _jload(); db["owners"].append(uid); _jsave(db)
    return True

def remove_owner(uid):
    if uid == config.OWNER_ID: return False
    _ensure()
    if uid not in _owners: return False
    _owners.remove(uid)
    if USE_MONGO: _fire(_col_acl.update_one({"_id":"owners"}, {"$pull":{"list":uid}}))
    else:
        db = _jload()
        if uid in db["owners"]: db["owners"].remove(uid)
        _jsave(db)
    return True

def add_admin(uid):
    _ensure()
    if uid in _admins: return False
    _admins.append(uid)
    if USE_MONGO: _fire(_col_acl.update_one({"_id":"admins"}, {"$addToSet":{"list":uid}}, upsert=True))
    else:
        db = _jload(); db["admins"].append(uid); _jsave(db)
    return True

def remove_admin(uid):
    _ensure()
    if uid not in _admins: return False
    _admins.remove(uid)
    if USE_MONGO: _fire(_col_acl.update_one({"_id":"admins"}, {"$pull":{"list":uid}}))
    else:
        db = _jload()
        if uid in db["admins"]: db["admins"].remove(uid)
        _jsave(db)
    return True

def list_users(): _ensure(); return {"owners": list(_owners or []), "admins": list(_admins or [])}

# ── started ───────────────────────────────────────────────────
def has_started(uid):
    _ensure()
    return uid in (_started or set())

def mark_started(uid):
    _ensure()
    if uid in _started: return
    _started.add(uid)
    if USE_MONGO: _fire(_col_s.update_one({"_id": uid}, {"$set": {"_id": uid}}, upsert=True))
    else:
        db = _jload()
        if "started" not in db: db["started"] = []
        if uid not in db["started"]:
            db["started"].append(uid); _jsave(db)

# ── settings ──────────────────────────────────────────────────
def get_settings(uid):
    if uid in _settings: return dict(_settings[uid])
    if not USE_MONGO:
        db = _jload()
        s = {**_def(), **db["settings"].get(str(uid), {})}
        _settings[uid] = s
        return dict(s)
    # async load not possible here — return defaults, background load
    s = _def(); _settings[uid] = s
    async def _load():
        doc = await _col_u.find_one({"_id": uid})
        if doc:
            doc.pop("_id", None)
            _settings[uid] = {**_def(), **doc}
    _fire(_load())
    return dict(s)

async def get_settings_async(uid):
    if uid in _settings: return dict(_settings[uid])
    if USE_MONGO:
        doc = await _col_u.find_one({"_id": uid})
        s = {**_def(), **(doc or {})}
        s.pop("_id", None)
    else:
        db = _jload()
        s = {**_def(), **db["settings"].get(str(uid), {})}
    _settings[uid] = s
    return dict(s)

def update_settings(uid, **kw):
    s = _settings.get(uid, _def())
    s.update(kw); _settings[uid] = s
    if USE_MONGO:
        _fire(_col_u.update_one({"_id": uid}, {"$set": kw}, upsert=True))
    else:
        db = _jload(); k = str(uid)
        if k not in db["settings"]: db["settings"][k] = _def()
        db["settings"][k].update(kw); _jsave(db)

def reset_settings(uid):
    _settings.pop(uid, None)
    if USE_MONGO: _fire(_col_u.delete_one({"_id": uid}))
    else:
        db = _jload(); db["settings"].pop(str(uid), None); _jsave(db)
