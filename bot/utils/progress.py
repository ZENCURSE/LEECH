"""
progress.py — Modern progress cards for NXT HUB
"""
import config
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.size_utils import human_size, human_speed, human_time

# ── Bar characters ────────────────────────────────────────────
_F = "🔴"
_E = "⭕"
_W = 12

def _bar(pct: float) -> str:
    pct = max(0.0, min(100.0, pct))
    n   = round(_W * pct / 100)
    return _F * n + _E * (_W - n)

def _pct_label(pct: float) -> str:
    return f"{pct:5.1f}%"

# ── Separator ─────────────────────────────────────────────────
SEP = "━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Status styles ─────────────────────────────────────────────
_STYLE = {
    "downloading": ("⬇️", "DOWNLOADING"),
    "uploading":   ("📤", "UPLOADING"),
    "processing":  ("⚙️", "PROCESSING"),
    "queued":      ("🕐", "QUEUED"),
    "cancelled":   ("🚫", "CANCELLED"),
    "done":        ("✅", "DONE"),
    "error":       ("❌", "ERROR"),
}

def cancel_cmd(tid: str) -> str:
    return f"<code>/c1_{tid.lower()}</code>"

def task_kb(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"prog_refresh:{task_id}"),
        InlineKeyboardButton("❌ Cancel",  callback_data=f"prog_cancel:{task_id}"),
    ]])

def group_task_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"grp_refresh:{uid}"),
    ]])

# ── Slots line ────────────────────────────────────────────────
def _slots_line() -> str:
    from bot.core.task_manager import stats
    s = stats()
    if s.get("total_slots", 0) > 0:
        return f"🟢 <b>{s['active']}/{s['total_slots']}</b> active  🟡 <b>{s['queued']}</b> queued"
    return f"🟢 <b>{s['active']}</b> active  🟡 <b>{s['queued']}</b> queued"

def _total_speed() -> float:
    from bot.core.task_manager import stats
    return stats().get("total_speed", 0.0)


# ══════════════════════════════════════════════════════════════
#  DOWNLOADING / UPLOADING card  — all bold, full filename
# ══════════════════════════════════════════════════════════════
def _active_card(status: str, name: str, done: int, total: int,
                 speed: float, eta: float, tid: str) -> str:
    icon, label = _STYLE.get(status, ("⚙️", status.upper()))
    pct         = (done / total * 100) if total else 0

    lines = [
        f"<b>{SEP}</b>",
        f"<b>{icon} {label}</b>",
        f"<b>{SEP}</b>",
        "",
        f"🎬 <b>{name}</b>",
        "",
        f"<b><code>{_bar(pct)}</code>  {_pct_label(pct)}</b>",
        "",
        f"📦 <b>{human_size(done)}</b> of <b>{human_size(total)}</b>",
        f"⚡ <b>{human_speed(speed)}</b>",
        f"⏳ ETA <b>{human_time(eta)}</b>",
        "",
        f"🆔 <b><code>{tid}</code></b>",
        f"✖️ Stop → {cancel_cmd(tid)}",
        "",
        f"<b>{SEP}</b>",
        f"<b>⚡ {config.WATERMARK}</b>",
    ]
    return "\n".join(lines)


def downloading_card(name, done, total, speed, eta, tid):
    return _active_card("downloading", name, done, total, speed, eta, tid)

def uploading_card(name, done, total, speed, eta, tid):
    return _active_card("uploading",   name, done, total, speed, eta, tid)


# ══════════════════════════════════════════════════════════════
#  DONE card  — full bold, full filename
# ══════════════════════════════════════════════════════════════
def done_card(name: str, size: int, elapsed: float, avg_speed: float,
              tid: str, username: str = "") -> str:
    stem = (name[:38] + "…") if len(name) > 40 else name
    return "\n".join(filter(None, [
        f"<b>{SEP}</b>",
        "<b>✅  UPLOAD COMPLETE</b>",
        f"<b>{SEP}</b>",
        "",
        f"🎬 <b>{stem}</b>",
        "",
        f"<b><code>{'🔴' * 12}</code>  100.0%</b>",
        "",
        f"📦 <b>{human_size(size)}</b>",
        f"⚡ <b>{human_speed(avg_speed)}</b>",
        f"⏱ <b>{human_time(elapsed)}</b>",
        f"👤 <b>{username}</b>" if username else None,
        "",
        f"<b>{SEP}</b>",
        f"<b>⚡ {config.WATERMARK}</b>",
    ]))


# ══════════════════════════════════════════════════════════════
#  COMPLETION card  — full bold, full filename
# ══════════════════════════════════════════════════════════════
def completion_card(filename: str, size: int, elapsed: float, username: str) -> str:
    stem = (filename[:34] + "…") if len(filename) > 36 else filename
    return "\n".join([
        "╔═══════════════════════════╗",
        "║   🎉  <b>TASK COMPLETED</b>  🎉   ║",
        "╚═══════════════════════════╝",
        "",
        f"🎬  <code>{stem}</code>",
        "",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        f"📦  <b>Size</b>      {human_size(size)}",
        f"⏱  <b>Time</b>      {human_time(elapsed)}",
        f"👤  <b>User</b>      {username}",
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        "",
        f"<b>⚡ {config.WATERMARK}</b>",
    ])


# ══════════════════════════════════════════════════════════════
#  ERROR card
# ══════════════════════════════════════════════════════════════
def cancel_card(tid: str, name: str = "") -> str:
    stem = (name[:30] + "…") if len(name) > 32 else name
    lines = [
        f"<b>{SEP}</b>",
        "<b>🚫 TASK CANCELLED</b>",
        f"<b>{SEP}</b>",
        "",
    ]
    if stem:
        lines += [f"📄 <code>{stem}</code>", ""]
    lines += [
        f"🆔 <code>{tid}</code>",
        "",
        "↩️ <i>Send a new link to start again.</i>",
        "",
        f"<b>{SEP}</b>",
        f"<b>⚡ {config.WATERMARK}</b>",
    ]
    return "\n".join(lines)


def error_card(tid: str, error) -> str:
    return "\n".join([
        f"<b>{SEP}</b>",
        "<b>❌ ERROR</b>",
        f"<b>{SEP}</b>",
        "",
        f"🆔 <b><code>{tid}</code></b>",
        f"💬 <code>{str(error)[:280]}</code>",
        "",
        f"<b>{SEP}</b>",
        f"<b>⚡ {config.WATERMARK}</b>",
    ])


# ══════════════════════════════════════════════════════════════
#  GROUP / STATUS task block  — bold, separator, full filename
# ══════════════════════════════════════════════════════════════
def _task_block(num: int, tid: str, task: dict) -> str:
    p      = task.get("progress", {})
    status = task.get("status", "queued")
    icon, label = _STYLE.get(status, ("⚙️", status.upper()))
    name   = p.get("name") or task.get("name") or "…"
    done   = p.get("done",  0)
    total  = p.get("total", 0)
    speed  = p.get("speed", 0.0)
    eta    = p.get("eta",   0.0)
    pct    = (done / total * 100) if total else 0

    lines = [
        f"<b>{num}. {icon} {label}</b>",
        f"🎬 <b>{name}</b>",
    ]

    if status in ("downloading", "uploading"):
        lines += [
            f"<b><code>{_bar(pct)}</code>  {_pct_label(pct)}</b>",
            f"📦 <b>{human_size(done)}</b> / <b>{human_size(total)}</b>",
            f"⚡ <b>{human_speed(speed)}</b>  ⏳ <b>{human_time(eta)}</b>",
        ]

    lines += [
        f"🆔 <b><code>{tid}</code></b>  ✖️ {cancel_cmd(tid)}",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  GROUP card  (single message, all user tasks)
# ══════════════════════════════════════════════════════════════
def group_task_card(uid: int, uploading_to_pm: bool = False) -> str:
    from bot.core import task_manager as tm
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}

    header_lines = [
        f"<b>{SEP}</b>",
        f"<b>🚀 NXT HUB</b>",
        f"{_slots_line()}",
        f"⚡ Speed: <b>{human_speed(_total_speed())}</b>",
    ]
    if uploading_to_pm:
        header_lines.append("📨 <i>Sending to your PM…</i>")

    header_lines.append(f"<b>{SEP}</b>")

    if not tasks:
        return "\n".join(header_lines) + "\n\n📭 <i>No active tasks.</i>"

    header  = "\n".join(header_lines)
    # Each task block gets its own separator above and below
    divider = f"\n<b>{SEP}</b>\n"
    blocks  = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + divider.join(blocks) + f"\n<b>{SEP}</b>\n<b>⚡ {config.WATERMARK}</b>"


# ══════════════════════════════════════════════════════════════
#  /status  card
# ══════════════════════════════════════════════════════════════
def status_message(tasks: dict) -> str:
    header = "\n".join([
        f"<b>{SEP}</b>",
        "<b>📊 NXT HUB — STATUS</b>",
        f"{_slots_line()}",
        f"⚡ Speed: <b>{human_speed(_total_speed())}</b>",
        f"<b>{SEP}</b>",
    ])

    if not tasks:
        return header + "\n\n📭 <i>No active tasks.</i>"

    divider = f"\n<b>{SEP}</b>\n"
    blocks  = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + divider.join(blocks) + f"\n<b>{SEP}</b>\n<b>⚡ {config.WATERMARK}</b>"
    
