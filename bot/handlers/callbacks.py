"""Callback handlers — prog_refresh and prog_cancel only.
tf_* handlers live in download.py.
status_refresh / grp_refresh live in status.py.
Keeping duplicates here caused double-fire bugs.
"""
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import CallbackQuery

from bot.core import task_manager as tm
from bot.utils.progress import build_progress_card, task_kb, cancel_card, error_card


@Client.on_callback_query(filters.regex(r"^prog_refresh:"))
async def prog_refresh_cb(client: Client, cb: CallbackQuery):
    tid  = cb.data.split(":", 1)[1]
    task = tm.get_task(tid)
    if not task:
        return await cb.answer("✅ Task already finished.", show_alert=True)
    if task["status"] == "cancelled":
        return await cb.answer("🚫 Already cancelled.", show_alert=True)
    p    = tm.get_progress(tid)
    name, done, tot, spd, eta = (
        p.get("name", "..."), p.get("done", 0), p.get("total", 0),
        p.get("speed", 0.0), p.get("eta", 0.0),
    )
    pct = (done / tot * 100) if tot else 0
    card = build_progress_card(
        task["status"], name, pct,
        done=done, total=tot, speed=spd, eta=eta, tid=tid,
        parent_name=p.get("parent_name", ""),
        part_num=p.get("part_num", 0),
        part_total=p.get("part_total", 0),
        user_mention=tm.get_user_mention(tid),
    )
    try:
        await cb.message.edit_text(card, reply_markup=task_kb(tid), parse_mode=enums.ParseMode.HTML)
        await cb.answer("🔄 Refreshed!")
    except Exception:
        await cb.answer("No change.")


@Client.on_callback_query(filters.regex(r"^prog_cancel:"))
async def prog_cancel_cb(client: Client, cb: CallbackQuery):
    tid  = cb.data.split(":", 1)[1]
    task = tm.get_task(tid)
    if not task:
        return await cb.answer("✅ Task already finished.", show_alert=True)
    if cb.from_user.id != task["user_id"]:
        return await cb.answer("❌ Not your task.", show_alert=True)
    if task["status"] == "cancelled":
        return await cb.answer("Already cancelled.", show_alert=True)

    tm.cancel_task(tid)
    await cb.answer("🚫 Cancelled!")

    try:
        await cb.message.edit_text(
            cancel_card(tid),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        pass
