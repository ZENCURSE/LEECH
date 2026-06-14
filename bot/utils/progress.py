"""
progress.py — NXT HUB  |  Unique card-style progress UI
"""
import time
import config
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.size_utils import human_size, human_speed, human_time

# ── Progress bar using block squares ──────────────────────────
def bar(pct: float, width: int = 15) -> str:
    filled = int(width * pct / 100)
    return "⬛" * filled + "⬜" * (width - filled)

# ── Status meta ───────────────────────────────────────────────
_STYLE = {
    "downloading": ("⬇️",  "DOWNLOADING"),
    "uploading":   ("📤",  "UPLOADING"),
    "processing":  ("⚙️",  "PROCESSING"),
    "queued":      ("🕐",  "QUEUED"),
    "cancelled":   ("🚫",  "CANCELLED"),
    "done":        ("✅",  "COMPLETE"),
    "error":       ("❌",  "FAILED"),
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
#  Box-frame design with full filename + arrow-led stats
# ══════════════════════════════════════════════════════════════
def _active_card(status: str, name: str, done: int, total: int,
                 speed: float, eta: float, tid: str,
                 started: float = 0.0) -> str:
    icon, label = _STYLE.get(status, ("⚙️", status.upper()))
    pct     = (done / total * 100) if total else 0
    elapsed = time.time() - started if started else 0.0

    lines = [
        f"╔═「 {icon} <b>{label}</b> 」",
        f"║",
        f"║  🎬 <b>{name}</b>",
        f"║",
        f"║  <code>{bar(pct)}</code>  <b>{pct:.1f}%</b>",
        f"║",
        f"╠═「 📊 <b>STATS</b> 」",
        f"║  ➤ <b>Size</b>   :  {human_size(done)}  /  {human_size(total)}",
        f"║  ➤ <b>Speed</b>  :  {human_speed(speed)}",
        f"║  ➤ <b>ETA</b>    :  {human_time(eta)}",
    ]
    if elapsed > 2:
        lines.append(f"║  ➤ <b>Elapsed</b> :  {human_time(elapsed)}")
    lines += [
        f"║  ➤ <b>Task</b>   :  <code>#{tid}</code>",
        f"╚══════════════════════",
        f"  ✖️ Cancel → {cancel_cmd(tid)}",
        f"  <i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)

def downloading_card(name, done, total, speed, eta, tid, started=0.0):
    return _active_card("downloading", name, done, total, speed, eta, tid, started)

def uploading_card(name, done, total, speed, eta, tid, started=0.0):
    return _active_card("uploading",   name, done, total, speed, eta, tid, started)


# ══════════════════════════════════════════════════════════════
#  PROCESSING card
# ══════════════════════════════════════════════════════════════
def processing_card(name: str, tid: str, step: str = "Encoding…") -> str:
    return "\n".join([
        f"╔═「 ⚙️ <b>PROCESSING</b> 」",
        f"║",
        f"║  🎬 <b>{name}</b>",
        f"║",
        f"║  <code>{'⬜' * 15}</code>  <b>working…</b>",
        f"║",
        f"╠═「 🔧 <b>STEP</b> 」",
        f"║  ➤ {step}",
        f"║  ➤ <b>Task</b> :  <code>#{tid}</code>",
        f"╚══════════════════════",
        f"  ✖️ Cancel → {cancel_cmd(tid)}",
        f"  <i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  DONE card
# ══════════════════════════════════════════════════════════════
def done_card(name: str, size: int, elapsed: float, avg_speed: float,
              tid: str, username: str = "") -> str:
    lines = [
        f"╔═「 ✅ <b>COMPLETE</b> 」",
        f"║",
        f"║  🎬 <b>{name}</b>",
        f"║",
        f"║  <code>{'⬛' * 15}</code>  <b>100%</b>",
        f"║",
        f"╠═「 📊 <b>STATS</b> 」",
        f"║  ➤ <b>Size</b>    :  {human_size(size)}",
        f"║  ➤ <b>Speed</b>   :  {human_speed(avg_speed)}",
        f"║  ➤ <b>Time</b>    :  {human_time(elapsed)}",
        f"║  ➤ <b>Task</b>    :  <code>#{tid}</code>",
    ]
    if username:
        lines.append(f"║  ➤ <b>By</b>      :  {username}")
    lines += [
        f"╚══════════════════════",
        f"  <i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  COMPLETION card  (PM notification)
# ══════════════════════════════════════════════════════════════
def completion_card(filename: str, size: int, elapsed: float, username: str) -> str:
    return "\n".join([
        f"╔═「 🎉 <b>TASK DONE</b> 」",
        f"║",
        f"║  🎬 <b>{filename}</b>",
        f"║",
        f"╠═「 📊 <b>STATS</b> 」",
        f"║  ➤ <b>Size</b>  :  {human_size(size)}",
        f"║  ➤ <b>Time</b>  :  {human_time(elapsed)}",
        f"║  ➤ <b>By</b>    :  {username}",
        f"╚══════════════════════",
        f"  <i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  QUEUED card
# ══════════════════════════════════════════════════════════════
def queued_card(name: str, tid: str, position: int = 0) -> str:
    pos_str = f"Position #{position}" if position else "Waiting to start…"
    return "\n".join([
        f"╔═「 🕐 <b>QUEUED</b> 」",
        f"║",
        f"║  🎬 <b>{name}</b>",
        f"║",
        f"║  <code>{'⬜' * 15}</code>  <b>waiting</b>",
        f"║",
        f"╠═「 📋 <b>INFO</b> 」",
        f"║  ➤ <b>Queue</b>  :  {pos_str}",
        f"║  ➤ <b>Task</b>   :  <code>#{tid}</code>",
        f"╚══════════════════════",
        f"  ✖️ Cancel → {cancel_cmd(tid)}",
        f"  <i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  CANCEL card
# ══════════════════════════════════════════════════════════════
def cancel_card(tid: str, name: str = "") -> str:
    lines = [
        f"╔═「 🚫 <b>CANCELLED</b> 」",
        f"║",
    ]
    if name:
        lines.append(f"║  🎬 <b>{name}</b>")
        lines.append(f"║")
    lines += [
        f"║  ➤ <b>Task</b>  :  <code>#{tid}</code>",
        f"╚══════════════════════",
        f"  <i>Send a new link whenever you're ready.</i>",
        f"  <i>{config.WATERMARK}</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  ERROR card
# ══════════════════════════════════════════════════════════════
def error_card(tid: str, error) -> str:
    err_str = str(error)
    short   = err_str[:200] + ("…" if len(err_str) > 200 else "")
    return "\n".join([
        f"╔═「 ❌ <b>FAILED</b> 」",
        f"║",
        f"║  ➤ <b>Task</b>  :  <code>#{tid}</code>",
        f"║",
        f"╠═「 🔍 <b>ERROR</b> 」",
        f"║  <code>{short}</code>",
        f"╚══════════════════════",
        f"  <i>Try again or contact support.</i>",
        f"  <i>{config.WATERMARK}</i>",
    ])


# ══════════════════════════════════════════════════════════════
#  TASK BLOCK  —  compact row inside /status & group card
# ══════════════════════════════════════════════════════════════
def _task_block(num: int, tid: str, task: dict) -> str:
    p      = task.get("progress", {})
    status = task.get("status", "queued")
    icon, label = _STYLE.get(status, ("⚙️", status.upper()))

    name  = p.get("name") or task.get("name") or "…"
    done  = p.get("done",  0)
    total = p.get("total", 0)
    speed = p.get("speed", 0.0)
    eta   = p.get("eta",   0.0)
    pct   = (done / total * 100) if total else 0

    lines = [
        f"┌─ <b>{num}.</b> {icon}  <b>{label}</b>",
        f"│",
        f"│  🎬 <b>{name}</b>",
        f"│",
    ]

    if status in ("downloading", "uploading"):
        lines += [
            f"│  <code>{bar(pct, 12)}</code>  <b>{pct:.0f}%</b>",
            f"│  ➤ {human_size(done)} / {human_size(total)}",
            f"│  ➤ ⚡ {human_speed(speed)}  ·  ⏳ {human_time(eta)}",
        ]
    elif status == "queued":
        lines.append(f"│  ➤ 🕐 Waiting in queue…")
    elif status == "processing":
        lines.append(f"│  ➤ ⚙️ Processing…")

    lines += [
        f"│",
        f"└─ ✖️  {cancel_cmd(tid)}",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  GROUP card  (shown in the group chat)
# ══════════════════════════════════════════════════════════════
def group_task_card(uid: int, uploading_to_pm: bool = False) -> str:
    from bot.core import task_manager as tm
    tasks = {tid: d for tid, d in tm.all_tasks().items() if d["user_id"] == uid}

    spd = _total_speed()
    header_lines = [
        f"╔══════════════════════════╗",
        f"║   🚀  <b>NXT HUB</b>  STATUS      ║",
        f"╚══════════════════════════╝",
        _slots_line(),
        f"⚡  <b>Total Speed</b>  :  {human_speed(spd)}",
    ]
    if uploading_to_pm:
        header_lines.append("📨 <i>Uploading to your PM…</i>")
    header = "\n".join(header_lines)

    if not tasks:
        return header + "\n\n📭 <i>No active tasks.</i>"

    blocks = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n" + "\n\n".join(blocks) + f"\n\n<i>{config.WATERMARK}</i>"


# ══════════════════════════════════════════════════════════════
#  /status card
# ══════════════════════════════════════════════════════════════
def status_message(tasks: dict) -> str:
    spd   = _total_speed()
    count = len(tasks)
    header = "\n".join([
        f"╔══════════════════════════╗",
        f"║   📊  <b>MY TASKS</b>" + (f"  ·  <b>{count}</b> active" if count else "") + "      ║",
        f"╚══════════════════════════╝",
        _slots_line(),
        f"⚡  <b>Total Speed</b>  :  {human_speed(spd)}",
        f"",
    ])

    if not tasks:
        return header + "📭 <i>No active tasks right now.</i>"

    blocks = [_task_block(i, tid, task) for i, (tid, task) in enumerate(tasks.items(), 1)]
    return header + "\n\n".join(blocks) + f"\n\n<i>{config.WATERMARK}</i>"
