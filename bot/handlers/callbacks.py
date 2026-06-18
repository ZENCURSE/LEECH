"""Callback handlers — prog_refresh and prog_cancel only.
tf_* handlers live in download.py.
status_refresh / grp_refresh live in status.py.
Keeping duplicates here caused double-fire bugs.
"""
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import CallbackQuery

from bot.core import task_manager as tm
from bot.utils.progress import downloading_card, uploading_card, task_kb, cancel_card, error_card


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
    card = (uploading_card if task["status"] == "uploading"
            else downloading_card)(name, done, tot, spd, eta, tid)
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
