"""Callback handlers — updated for new downloader API."""
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import CallbackQuery

from bot.core import task_manager as tm
from bot.core.downloader import (
    torrent_download, torrent_set_selected, torrent_resume, torrent_remove,
    torrent_get_files, torrent_get_real_gid, torrent_pause,
)
from bot.utils.progress import downloading_card, uploading_card, task_kb, status_message
from bot.handlers.download import get_pending, get_selection, get_finish_torrent, get_build_file_kb


@Client.on_callback_query(filters.regex(r"^prog_refresh:"))
async def prog_refresh_cb(client: Client, cb: CallbackQuery):
    tid  = cb.data.split(":", 1)[1]
    task = tm.get_task(tid)
    if not task:
        return await cb.answer("✅ Task already finished.", show_alert=True)
    # Anyone can refresh — no ownership check
    if task["status"] == "cancelled":
        return await cb.answer("🚫 Already cancelled.", show_alert=True)
    p    = tm.get_progress(tid)
    name, done, tot, spd, eta = (
        p.get("name","..."), p.get("done",0), p.get("total",0),
        p.get("speed",0.0), p.get("eta",0.0),
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

    # Hard-cancel: sets event AND cancels the asyncio coroutine
    tm.cancel_task(tid)

    # Also stop aria2 torrent if running
    if gid := task.get("gid"):
        try:
            await torrent_remove(gid)
        except Exception:
            pass

    await cb.answer("🚫 Cancelled!")

    # Update the progress card immediately — the coroutine may take a moment to clean up
    try:
        await cb.message.edit_text(
            f"🚫 <b>Task Cancelled</b>\n🆔 <b><code>{tid}</code></b>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=None,
        )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^tf_"))
async def torrent_file_cb(client: Client, cb: CallbackQuery):
    parts    = cb.data.split(":")
    action   = parts[1]
    gid      = parts[2]
    pending  = get_pending()
    selection = get_selection()
    finish   = get_finish_torrent()
    build_kb = get_build_file_kb()

    if gid not in pending:
        return await cb.answer("⏱ Session expired. Re-send the link.", show_alert=True)
    ctx = pending[gid]
    if cb.from_user.id != ctx["uid"]:
        return await cb.answer("❌ Not your task.", show_alert=True)

    files = await torrent_get_files(gid)

    if action == "toggle":
        idx = int(parts[3])
        if idx in selection[gid]:
            selection[gid].discard(idx)
        else:
            selection[gid].add(idx)
        await cb.message.edit_reply_markup(build_kb(gid, files))
        await cb.answer()

    elif action == "all":
        selection[gid] = set(f["index"] for f in files)
        await cb.message.edit_reply_markup(build_kb(gid, files))
        await cb.answer("✅ All selected.")

    elif action == "none":
        selection[gid] = set()
        await cb.message.edit_reply_markup(build_kb(gid, files))
        await cb.answer("☐ All deselected.")

    elif action == "start":
        selected = sorted(selection.get(gid, set()))
        if not selected:
            return await cb.answer("⚠️ Select at least one file.", show_alert=True)
        await cb.answer("⬇️ Starting download...")
        pending.pop(gid, None)
        selection.pop(gid, None)

        tid = ctx["tid"]
        msg = ctx["msg"]

        try:
            await msg.edit_text(
                f"⬇️ Downloading {len(selected)} file(s)...\n🆔 <code>{tid}</code>",
                reply_markup=task_kb(tid), parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        try:
            # Must pause before changing select-file option (aria2 requirement)
            real_gid = await torrent_get_real_gid(gid)
            await torrent_pause(real_gid)
            await asyncio.sleep(0.5)  # brief wait for pause to take effect
            await torrent_set_selected(real_gid, selected)
            await torrent_resume(real_gid)
            paths = await torrent_download(
                ctx["src"], ctx["dest_dir"], tid, msg,
                ctx["is_magnet"], existing_gid=real_gid,
            )
            await finish(client, ctx["message"], msg, paths,
                         ctx["dest_dir"], tid, ctx["uid"], ctx["action"], ctx["start"],
                         ctx.get("is_group", False))
        except asyncio.CancelledError:
            from bot.utils.progress import cancel_card
            uid = ctx.get("uid")
            try:
                await msg.edit_text(cancel_card(tid), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            if uid:
                try:
                    from bot import app as bot_app
                    await bot_app.send_message(uid, cancel_card(tid), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
            tm.finish_task(tid)
        except Exception as e:
            from bot.utils.progress import error_card, cancel_card
            uid = ctx.get("uid")
            try:
                await msg.edit_text(error_card(tid, e), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            if uid:
                try:
                    from bot import app as bot_app
                    await bot_app.send_message(uid, error_card(tid, e), parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
            tm.finish_task(tid)


@Client.on_callback_query(filters.regex(r"^status_refresh:"))
async def status_refresh_cb(client: Client, cb: CallbackQuery):
    uid = int(cb.data.split(":", 1)[1])
    if cb.from_user.id != uid:
        return await cb.answer("Not your status.", show_alert=True)
    from bot.handlers.status import _status_kb
    tasks = {tid: data for tid, data in tm.all_tasks().items()
             if data["user_id"] == uid}
    try:
        await cb.message.edit_text(
            status_message(tasks),
            reply_markup=_status_kb(uid), parse_mode=enums.ParseMode.HTML,
        )
        await cb.answer("🔄 Refreshed!")
    except Exception:
        await cb.answer()
        
