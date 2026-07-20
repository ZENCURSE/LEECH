"""
Central task registry.
Each task stores a live progress snapshot so Refresh and /status
can read it at any time without blocking.

Limits:
  config.MAX_TASKS   — per-user concurrent task cap
  config.TOTAL_TASKS — global concurrent task cap (0 = unlimited)
"""
import asyncio
import random
import string
import time
import config

_tasks: dict[str, dict] = {}


def _new_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _active_statuses():
    return ("queued", "downloading", "uploading", "processing")


def user_task_count(uid: int) -> int:
    return sum(
        1 for t in _tasks.values()
        if t["user_id"] == uid and t["status"] in _active_statuses()
    )


def global_task_count() -> int:
    return sum(
        1 for t in _tasks.values()
        if t["status"] in _active_statuses()
    )


def can_add_task(uid: int) -> tuple[bool, str]:
    """
    Returns (True, "") if a new task can be created.
    Returns (False, reason) otherwise.
    """
    if user_task_count(uid) >= config.MAX_TASKS:
        return False, "user"
    if config.TOTAL_TASKS > 0 and global_task_count() >= config.TOTAL_TASKS:
        return False, "global"
    return True, ""


def create_task(uid: int, name: str = "", user_mention: str = "") -> str:
    tid = _new_id()
    _tasks[tid] = {
        "user_id":      uid,
        "user_mention": user_mention,
        "status":       "queued",
        "name":         name,
        "cancel_event": asyncio.Event(),
        "asyncio_task": None,   # set after coroutine is scheduled
        "gid":          None,
        "created_at":   time.time(),
        "progress": {
            "name":   name,
            "done":   0,
            "total":  0,
            "speed":  0.0,
            "eta":    0.0,
            "status": "queued",
        },
    }
    return tid


def get_user_mention(tid: str) -> str:
    t = _tasks.get(tid)
    return (t or {}).get("user_mention", "")


def set_asyncio_task(tid: str, coro_task) -> None:
    """Store the asyncio.Task handle so cancel_task can hard-cancel it."""
    t = _tasks.get(tid)
    if t:
        t["asyncio_task"] = coro_task


def get_task(tid: str) -> dict | None:
    return _tasks.get(tid)


def all_tasks() -> dict[str, dict]:
    return dict(_tasks)


def set_status(tid: str, status: str) -> None:
    t = _tasks.get(tid)
    if t:
        t["status"] = status
        t["progress"]["status"] = status


def set_status_text(tid: str, text: str) -> None:
    """Store a human-readable status label shown in the status card."""
    t = _tasks.get(tid)
    if t:
        t["status_text"] = text


def set_gid(tid: str, gid: str) -> None:
    t = _tasks.get(tid)
    if t:
        t["gid"] = gid


def update_progress(tid: str, **kw) -> None:
    t = _tasks.get(tid)
    if t:
        t["progress"].update(kw)


def get_progress(tid: str) -> dict:
    t = _tasks.get(tid)
    return dict(t["progress"]) if t else {}


def cancel_task(tid: str) -> bool:
    t = _tasks.get(tid)
    if not t:
        return False
    t["cancel_event"].set()
    t["status"] = "cancelled"
    t["progress"]["status"] = "cancelled"
    # Hard-cancel the running asyncio coroutine so it stops immediately
    # even if it's blocked inside a long upload/download call.
    coro = t.get("asyncio_task")
    if coro is not None and not coro.done():
        coro.cancel()
    return True


def is_cancelled(tid: str) -> bool:
    t = _tasks.get(tid)
    return t is not None and t["cancel_event"].is_set()


def finish_task(tid: str) -> None:
    _tasks.pop(tid, None)


def get_user_tasks(uid: int) -> list[dict]:
    return [{"id": tid, **data} for tid, data in _tasks.items() if data["user_id"] == uid]


def stats() -> dict:
    """Global stats: active count, queued count, total speed, slot info."""
    active = queued = 0
    total_speed = 0.0
    for t in _tasks.values():
        s = t["status"]
        if s in ("downloading", "uploading", "processing"):
            active += 1
            total_speed += t["progress"].get("speed", 0.0)
        elif s == "queued":
            queued += 1
    return {
        "active":       active,
        "queued":       queued,
        "total_speed":  total_speed,
        "total_slots":  config.TOTAL_TASKS,   # 0 = unlimited
    }
