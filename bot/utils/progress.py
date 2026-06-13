"""
progress.py — Clean progress cards for NXT HUB
"""
import config
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.size_utils import human_size, human_speed, human_time, bar

# ── Dividers ──────────────────────────────────────────────────
_DIV  = "─" * 26
_BDIV = "━" * 26

# ── Status meta ───────────────────────────────────────────────
_STYLE = {
    "downloading": ("⬇️", "Downloading"),
    "uploading":   ("📤", "Uploading"),
    "processing":  ("⚙️", "Processing"),
    "queued":      ("🕐", "Queued"),
    "cancelled":   ("🚫", "Cancelled"),
    "done":        ("✅", "Done"),
    "error":       ("❌", "Error"),
}

# ── Keyboards ─────────────────────────────────────────────────
def task_kb(tid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"prog_refresh:{tid}"),
        InlineKeyboardButton("❌ Cancel",  callback_data=f"prog_cancel:{tid}"),
    ]])

def group_task_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"grp_refresh:{uid}"),
    ]])

# ── Cancel command helper (used in group cards) ───────────────
def cancel_cmd(tid: str) -> str:
    return f"<code>/c1_{tid.lower()}</code>"

# ── Slot / speed summary ──────────────────────────────────────
def _slots_line() -> str:
    from bot.core.task_manager import stats
    s = stats()
    active  = s.get("active", 0)
    queued  = s.get("queued", 0)
    slots   = s.get("total_slots", 0)
    slot_str = f"{active}/{slots}" if slots else str(active)
    return f"🟢 <b>{slot_str}</b> active  🟡 <b>{queued}</b> queued"

def _total_speed() -> float:
    from bot.core.task_manager import stats
    return stats().get("total_speed", 0.0)

# ── Name truncation ───────────────────────────────────────────
def _trim(name: str, n: int = 40) -> str:
    return (name[:n - 1] + "…") if len(name) > n else name


# ══════════════════════════════════════════════════════════════
#  ACTIVE card  (downloading / uploading)
# ══════════════════════════════════════════════════════════════
def _active_card(status: str, name: str, done: int, total: int,
                 speed: float, eta: float, tid: str) -> str:
    icon, label = _STYLE.get(status, ("⚙️", status.title()))
    pct = (done / total * 100) if total else 0
    progress_bar = bar(pct, 16)

    return "\n".join([
        f"{icon} <b>{label}</b>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name)}</b>",
        "",
        f"<code>[{progress_bar}]</code>  <b>{pct:.1f}%</b>",
        "",
        f"📦 {human_size(done)} / {human_size(total)}",
        f"⚡ {human_speed(speed)}   ⏳ ETA {human_time(eta)}",
        "",
        f"<b>{_DIV}</b>",
        f"🆔 <code>{tid}</code>   ✖️ {cancel_cmd(tid)}",
        f"<i>{config.WATERMARK}</i>",
    ])

def downloading_card(name, done, total, speed, eta, tid):
    return _active_card("downloading", name, done, total, speed, eta, tid)

def uploading_card(name, done, total, speed, eta, tid):
    return _active_card("uploading",   name, done, total, speed, eta, tid)


# ══════════════════════════════════════════════════════════════
#  DONE card
# ══════════════════════════════════════════════════════════════
def done_card(name: str, size: int, elapsed: float, avg_speed: float,
              tid: str, username: str = "") -> str:
    lines = [
        "✅ <b>Upload Complete</b>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(name)}</b>",
        "",
        f"<code>[{'█' * 16}]</code>  <b>100.0%</b>",
        "",
        f"📦 {human_size(size)}",
        f"⚡ Avg {human_speed(avg_speed)}   ⏱ {human_time(elapsed)}",
    ]
    if username:
        lines.append(f"👤 {username}")
    lines += [
        "",
        f"<i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  COMPLETION card  (final message sent to user)
# ══════════════════════════════════════════════════════════════
def completion_card(filename: str, size: int, elapsed: float, username: str) -> str:
    return "\n".join([
        "🎉 <b>Task Completed</b>",
        f"<b>{_BDIV}</b>",
        f"📄 <b>{_trim(filename, 36)}</b>",
        "",
        f"📦 {human_size(size)}   ⏱ {human_time(elapsed)}",
        f"👤 {username}",
        "",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  CANCEL card
# ══════════════════════════════════════════════════════════════
def cancel_card(tid: str, name: str = "") -> str:
    lines = [
        "🚫 <b>Task Cancelled</b>",
        f"<b>{_BDIV}</b>",
    ]
    if name:
        lines.append(f"📄 {_trim(name, 32)}")
    lines += [
        f"🆔 <code>{tid}</code>",
        "",
        "<i>Send a new link to start again.</i>",
        f"<i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  ERROR card
# ══════════════════════════════════════════════════════════════
def error_card(tid: str, error) -> str:
    return "\n".join([
        "❌ <b>Task Failed</b>",
        f"<b>{_BDIV}</b>",
        f"🆔 <code>{tid}</code>",
        "",
        f"<code>{str(error)[:260]}</code>",
        "",
        "<i>Try again or contact support.</i>",
        f"<i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  TASK BLOCK  (used inside status / group cards)
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
        f"<b>{num}. {icon} {label}</b>",
        f"📄 {_trim(name, 36)}",
    ]

    if status in ("downloading", "uploading"):
        lines += [
            f"<code>[{bar(pct, 14)}]</code> {pct:.0f}%",
            f"📦 {human_size(done)} / {human_size(total)}   ⚡ {human_speed(speed)}   ⏳ {human_time(eta)}",
        ]

    lines.append(f"🆔 <code>{tid}</code>   ✖️ {cancel_cmd(tid)}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  GROUP card
# ══════════════════════════════════════════════════════════════
def group_task_card(uid: int, uploading_to_pm: bool = False) -> str:
    from bot.core import task_manager as tm
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}

    header = "\n".join(filter(None, [
        f"🚀 <b>NXT HUB</b>",
        _slots_line(),
        f"⚡ {human_speed(_total_speed())}",
        "📨 <i>Sending to your PM…</i>" if uploading_to_pm else None,
        f"<b>{_BDIV}</b>",
    ]))

    if not tasks:
        return header + "\n\n📭 <i>No active tasks.</i>"

    divider = f"\n<b>{_DIV}</b>\n"
    blocks  = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + divider.join(blocks) + f"\n\n<i>{config.WATERMARK}</i>"


# ══════════════════════════════════════════════════════════════
#  /status card
# ══════════════════════════════════════════════════════════════
def status_message(tasks: dict) -> str:
    header = "\n".join([
        "📊 <b>My Tasks</b>",
        f"<b>{_BDIV}</b>",
        _slots_line(),
        f"⚡ {human_speed(_total_speed())}",
        f"<b>{_DIV}</b>",
    ])

    if not tasks:
        return header + "\n\n📭 <i>No active tasks.</i>"

    divider = f"\n<b>{_DIV}</b>\n"
    blocks  = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + divider.join(blocks) + f"\n\n<i>{config.WATERMARK}</i>"
