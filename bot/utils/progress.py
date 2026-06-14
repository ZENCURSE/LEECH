"""
progress.py — NXT HUB progress cards
"""
import time
import config
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.size_utils import human_size, human_speed, human_time, bar

# ── Dividers ──────────────────────────────────────────────────
_DIV  = "─" * 28
_BDIV = "━" * 28

# ── Status meta ───────────────────────────────────────────────
_STYLE = {
    "downloading": ("⬇️",  "Downloading"),
    "uploading":   ("📤",  "Uploading"),
    "processing":  ("⚙️",  "Processing"),
    "queued":      ("🕐",  "Queued"),
    "cancelled":   ("🚫",  "Cancelled"),
    "done":        ("✅",  "Done"),
    "error":       ("❌",  "Error"),
}

# ── Keyboards ─────────────────────────────────────────────────
def task_kb(tid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh",    callback_data=f"prog_refresh:{tid}"),
            InlineKeyboardButton("❌ Cancel",      callback_data=f"prog_cancel:{tid}"),
        ],
        [
            InlineKeyboardButton("📊 All Tasks",  callback_data=f"prog_status:{tid}"),
        ],
    ])

def group_task_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"grp_refresh:{uid}"),
    ]])

def cancel_cmd(tid: str) -> str:
    return f"<code>/c1_{tid.lower()}</code>"

# ── Shared helpers ────────────────────────────────────────────
def _trim(name: str, n: int = 42) -> str:
    return (name[:n - 1] + "…") if len(name) > n else name

def _slots_line() -> str:
    from bot.core.task_manager import stats
    s = stats()
    active   = s.get("active", 0)
    queued   = s.get("queued", 0)
    slots    = s.get("total_slots", 0)
    slot_str = f"{active}/{slots}" if slots else str(active)
    return f"🟢 <b>{slot_str}</b> active  ·  🕐 <b>{queued}</b> queued"

def _total_speed() -> float:
    from bot.core.task_manager import stats
    return stats().get("total_speed", 0.0)


# ══════════════════════════════════════════════════════════════
#  ACTIVE card  (downloading / uploading)
# ══════════════════════════════════════════════════════════════
def _active_card(status: str, name: str, done: int, total: int,
                 speed: float, eta: float, tid: str,
                 started: float = 0.0) -> str:
    icon, label = _STYLE.get(status, ("⚙️", status.title()))
    pct     = (done / total * 100) if total else 0
    elapsed = time.time() - started if started else 0.0
    remaining = total - done if total else 0

    return "\n".join([
        f"{icon} <b>{label}</b>  ·  🆔 <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name)}</b>",
        "",
        f"<code>{bar(pct, 18)}</code>  <b>{pct:.1f}%</b>",
        "",
        f"📦 <b>{human_size(done)}</b> of <b>{human_size(total)}</b>  ({human_size(remaining)} left)",
        f"⚡ <b>{human_speed(speed)}</b>  ·  ⏳ ETA <b>{human_time(eta)}</b>"
        + (f"  ·  🕒 <b>{human_time(elapsed)}</b> elapsed" if elapsed > 2 else ""),
        "",
        f"<i>✖️ Cancel → {cancel_cmd(tid)}</i>",
        f"<i>{config.WATERMARK}</i>",
    ])

def downloading_card(name, done, total, speed, eta, tid, started=0.0):
    return _active_card("downloading", name, done, total, speed, eta, tid, started)

def uploading_card(name, done, total, speed, eta, tid, started=0.0):
    return _active_card("uploading",   name, done, total, speed, eta, tid, started)


# ══════════════════════════════════════════════════════════════
#  PROCESSING card  (FFmpeg / zip / unzip)
# ══════════════════════════════════════════════════════════════
def processing_card(name: str, tid: str, step: str = "Encoding…") -> str:
    return "\n".join([
        f"⚙️ <b>Processing</b>  ·  🆔 <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name)}</b>",
        "",
        f"<code>{'░' * 18}</code>  <b>working…</b>",
        "",
        f"🔧 {step}",
        "",
        f"<i>✖️ Cancel → {cancel_cmd(tid)}</i>",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  DONE card
# ══════════════════════════════════════════════════════════════
def done_card(name: str, size: int, elapsed: float, avg_speed: float,
              tid: str, username: str = "") -> str:
    lines = [
        f"✅ <b>Upload Complete</b>  ·  🆔 <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name)}</b>",
        "",
        f"<code>{'█' * 18}</code>  <b>100.0%</b>",
        "",
        f"📦 {human_size(size)}  ·  ⚡ {human_speed(avg_speed)}  ·  ⏱ {human_time(elapsed)}",
    ]
    if username:
        lines.append(f"👤 {username}")
    lines += ["", f"<i>{config.WATERMARK}</i>"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  COMPLETION card  (notification sent to PM)
# ══════════════════════════════════════════════════════════════
def completion_card(filename: str, size: int, elapsed: float, username: str) -> str:
    return "\n".join([
        "🎉 <b>Task Completed!</b>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(filename, 38)}</b>",
        "",
        f"📦 {human_size(size)}  ·  ⏱ {human_time(elapsed)}",
        f"👤 {username}",
        "",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  QUEUED card
# ══════════════════════════════════════════════════════════════
def queued_card(name: str, tid: str, position: int = 0) -> str:
    pos_str = f"Position #{position} in queue" if position else "Waiting to start…"
    return "\n".join([
        f"🕐 <b>Queued</b>  ·  🆔 <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name)}</b>",
        "",
        f"<code>{'░' * 18}</code>  <b>waiting</b>",
        "",
        f"📋 {pos_str}",
        "",
        f"<i>✖️ Cancel → {cancel_cmd(tid)}</i>",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  CANCEL card
# ══════════════════════════════════════════════════════════════
def cancel_card(tid: str, name: str = "") -> str:
    lines = [
        f"🚫 <b>Task Cancelled</b>  ·  🆔 <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
    ]
    if name:
        lines.append(f"📄 {_trim(name, 34)}")
    lines += [
        "",
        "<i>Send a new link whenever you're ready.</i>",
        f"<i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  ERROR card
# ══════════════════════════════════════════════════════════════
def error_card(tid: str, error) -> str:
    err_str = str(error)
    short   = err_str[:220] + ("…" if len(err_str) > 220 else "")
    return "\n".join([
        f"❌ <b>Task Failed</b>  ·  🆔 <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        "",
        f"<code>{short}</code>",
        "",
        "<i>Try again or contact support if this persists.</i>",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  TASK BLOCK  (used inside /status and group cards)
# ══════════════════════════════════════════════════════════════
def _task_block(num: int, tid: str, task: dict) -> str:
    p      = task.get("progress", {})
    status = task.get("status", "queued")
    icon, label = _STYLE.get(status, ("⚙️", status.title()))
    name   = p.get("name") or task.get("name") or "…"
    done   = p.get("done",  0)
    total  = p.get("total", 0)
    speed  = p.get("speed", 0.0)
    eta    = p.get("eta",   0.0)
    pct    = (done / total * 100) if total else 0

    lines = [
        f"<b>{num}.</b> {icon} <b>{label}</b>  ·  <code>{tid}</code>",
        f"   📄 {_trim(name, 36)}",
    ]

    if status in ("downloading", "uploading"):
        lines += [
            f"   <code>{bar(pct, 14)}</code>  {pct:.0f}%",
            f"   📦 {human_size(done)}/{human_size(total)}  ⚡ {human_speed(speed)}  ⏳ {human_time(eta)}",
        ]
    elif status == "queued":
        lines.append("   🕐 Waiting in queue…")
    elif status == "processing":
        lines.append("   ⚙️ Processing…")

    lines.append(f"   ✖️ {cancel_cmd(tid)}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  GROUP card
# ══════════════════════════════════════════════════════════════
def group_task_card(uid: int, uploading_to_pm: bool = False) -> str:
    from bot.core import task_manager as tm
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}

    spd    = _total_speed()
    header = "\n".join(filter(None, [
        f"🚀 <b>NXT HUB</b>  ·  ⚡ {human_speed(spd)}",
        _slots_line(),
        "📨 <i>Uploading to your PM…</i>" if uploading_to_pm else None,
        f"<b>{_BDIV}</b>",
    ]))

    if not tasks:
        return header + "\n\n📭 <i>No active tasks.</i>"

    blocks = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + f"\n<b>{_DIV}</b>\n".join(blocks) + f"\n\n<i>{config.WATERMARK}</i>"


# ══════════════════════════════════════════════════════════════
#  /status card
# ══════════════════════════════════════════════════════════════
def status_message(tasks: dict) -> str:
    spd    = _total_speed()
    count  = len(tasks)
    header = "\n".join([
        f"📊 <b>My Tasks</b>" + (f"  ·  <b>{count}</b> running" if count else ""),
        f"<b>{_BDIV}</b>",
        _slots_line(),
        f"⚡ Total speed: <b>{human_speed(spd)}</b>",
        f"<b>{_DIV}</b>",
    ])

    if not tasks:
        return header + "\n\n📭 <i>No active tasks right now.</i>"

    blocks = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + f"\n<b>{_DIV}</b>\n".join(blocks) + f"\n\n<i>{config.WATERMARK}</i>"
