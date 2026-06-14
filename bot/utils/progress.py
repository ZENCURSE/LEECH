"""
progress.py — NXT HUB progress cards
"""
import time
import config
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.size_utils import human_size, human_speed, human_time, bar

# ── Dividers ──────────────────────────────────────────────────
_DIV  = "─" * 24
_BDIV = "━" * 24

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
            InlineKeyboardButton("🔄 Refresh",   callback_data=f"prog_refresh:{tid}"),
            InlineKeyboardButton("❌ Cancel",     callback_data=f"prog_cancel:{tid}"),
        ],
        [
            InlineKeyboardButton("📊 All Tasks", callback_data=f"prog_status:{tid}"),
        ],
    ])

def group_task_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"grp_refresh:{uid}"),
    ]])

def cancel_cmd(tid: str) -> str:
    return f"<code>/c1_{tid.lower()}</code>"

# ── Shared helpers ────────────────────────────────────────────
def _trim(name: str, n: int = 30) -> str:
    """Trim filename to n chars — keeps it on one line."""
    return (name[:n - 1] + "…") if len(name) > n else name

def _slots_line() -> str:
    from bot.core.task_manager import stats
    s = stats()
    active = s.get("active", 0)
    queued = s.get("queued", 0)
    slots  = s.get("total_slots", 0)
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

    size_line = f"📦 <b>{human_size(done)}</b> / <b>{human_size(total)}</b>"
    stat_line = f"⚡ <b>{human_speed(speed)}</b>  ·  ⏳ <b>{human_time(eta)}</b>"
    if elapsed > 2:
        stat_line += f"  ·  🕒 <b>{human_time(elapsed)}</b>"

    return "\n".join([
        f"{icon} <b>{label}</b>  ·  <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name, 32)}</b>",
        f"<code>{bar(pct, 16)}</code>  <b>{pct:.1f}%</b>",
        size_line,
        stat_line,
        f"<b>{_DIV}</b>",
        f"✖️ {cancel_cmd(tid)}  ·  <i>{config.WATERMARK}</i>",
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
        f"⚙️ <b>Processing</b>  ·  <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name, 32)}</b>",
        f"<code>{'░' * 16}</code>  <b>working…</b>",
        f"🔧 {step}",
        f"<b>{_DIV}</b>",
        f"✖️ {cancel_cmd(tid)}  ·  <i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  DONE card
# ══════════════════════════════════════════════════════════════
def done_card(name: str, size: int, elapsed: float, avg_speed: float,
              tid: str, username: str = "") -> str:
    lines = [
        f"✅ <b>Upload Complete</b>  ·  <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name, 32)}</b>",
        f"<code>{'█' * 16}</code>  <b>100%</b>",
        f"📦 {human_size(size)}  ·  ⚡ {human_speed(avg_speed)}  ·  ⏱ {human_time(elapsed)}",
    ]
    if username:
        lines.append(f"👤 {username}")
    lines += [f"<b>{_DIV}</b>", f"<i>{config.WATERMARK}</i>"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  COMPLETION card
# ══════════════════════════════════════════════════════════════
def completion_card(filename: str, size: int, elapsed: float, username: str) -> str:
    return "\n".join([
        "🎉 <b>Task Completed!</b>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(filename, 32)}</b>",
        f"📦 {human_size(size)}  ·  ⏱ {human_time(elapsed)}",
        f"👤 {username}",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  QUEUED card
# ══════════════════════════════════════════════════════════════
def queued_card(name: str, tid: str, position: int = 0) -> str:
    pos_str = f"#{position} in queue" if position else "Waiting to start…"
    return "\n".join([
        f"🕐 <b>Queued</b>  ·  <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name, 32)}</b>",
        f"<code>{'░' * 16}</code>  <b>waiting</b>",
        f"📋 {pos_str}",
        f"<b>{_DIV}</b>",
        f"✖️ {cancel_cmd(tid)}  ·  <i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  CANCEL card
# ══════════════════════════════════════════════════════════════
def cancel_card(tid: str, name: str = "") -> str:
    lines = [
        f"🚫 <b>Cancelled</b>  ·  <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
    ]
    if name:
        lines.append(f"📄 {_trim(name, 32)}")
    lines += [
        "<i>Send a new link whenever you're ready.</i>",
        f"<i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  ERROR card
# ══════════════════════════════════════════════════════════════
def error_card(tid: str, error) -> str:
    err_str = str(error)
    short   = err_str[:200] + ("…" if len(err_str) > 200 else "")
    return "\n".join([
        f"❌ <b>Failed</b>  ·  <code>{tid}</code>",
        f"<b>{_BDIV}</b>",
        f"<code>{short}</code>",
        "<i>Try again or contact support.</i>",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  TASK BLOCK  — compact single-task row for /status & group card
# ══════════════════════════════════════════════════════════════
def _task_block(num: int, tid: str, task: dict) -> str:
    p      = task.get("progress", {})
    status = task.get("status", "queued")
    icon, label = _STYLE.get(status, ("⚙️", status.title()))

    name  = p.get("name") or task.get("name") or "…"
    done  = p.get("done",  0)
    total = p.get("total", 0)
    speed = p.get("speed", 0.0)
    eta   = p.get("eta",   0.0)
    pct   = (done / total * 100) if total else 0

    # Header: "1. ⬇️ Downloading · ABC123"
    lines = [f"<b>{num}.</b> {icon} <b>{label}</b>  ·  <code>{tid}</code>"]

    # Filename — trimmed tight so it never wraps
    lines.append(f"   📄 <b>{_trim(name, 28)}</b>")

    if status in ("downloading", "uploading"):
        # Progress bar on its own line
        lines.append(f"   <code>{bar(pct, 12)}</code>  <b>{pct:.0f}%</b>")
        # Size and speed on one clean line
        lines.append(
            f"   📦 <b>{human_size(done)}</b>/<b>{human_size(total)}</b>"
            f"  ⚡ <b>{human_speed(speed)}</b>"
            f"  ⏳ <b>{human_time(eta)}</b>"
        )
    elif status == "queued":
        lines.append("   🕐 <i>Waiting in queue…</i>")
    elif status == "processing":
        lines.append("   ⚙️ <i>Processing…</i>")

    # Cancel command — compact, on its own line
    lines.append(f"   ✖️ {cancel_cmd(tid)}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  GROUP card  (shown in the chat where /d was triggered)
# ══════════════════════════════════════════════════════════════
def group_task_card(uid: int, uploading_to_pm: bool = False) -> str:
    from bot.core import task_manager as tm
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}

    spd = _total_speed()
    header_parts = [
        f"🚀 <b>NXT HUB</b>  ·  ⚡ {human_speed(spd)}",
        _slots_line(),
    ]
    if uploading_to_pm:
        header_parts.append("📨 <i>Uploading to your PM…</i>")
    header_parts.append(f"<b>{_BDIV}</b>")
    header = "\n".join(header_parts)

    if not tasks:
        return header + "\n\n📭 <i>No active tasks.</i>"

    blocks = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    sep    = f"\n<b>{_DIV}</b>\n"
    return header + "\n" + sep.join(blocks) + f"\n<b>{_BDIV}</b>\n<i>{config.WATERMARK}</i>"


# ══════════════════════════════════════════════════════════════
#  /status card
# ══════════════════════════════════════════════════════════════
def status_message(tasks: dict) -> str:
    spd   = _total_speed()
    count = len(tasks)
    header = "\n".join([
        f"📊 <b>My Tasks</b>" + (f"  ·  <b>{count}</b> active" if count else ""),
        _slots_line(),
        f"⚡ <b>{human_speed(spd)}</b> total",
        f"<b>{_BDIV}</b>",
    ])

    if not tasks:
        return header + "\n📭 <i>No active tasks right now.</i>"

    blocks = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    sep    = f"\n<b>{_DIV}</b>\n"
    return header + "\n" + sep.join(blocks) + f"\n<b>{_BDIV}</b>\n<i>{config.WATERMARK}</i>"
