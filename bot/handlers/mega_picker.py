"""
Mega Folder File Picker — Telegram inline button UI
Handles selecting files from a Mega folder before downloading.
Callback prefix: ms:{gid}:{action}
"""
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.utils.size_utils import human_size

# ── Pagination ────────────────────────────────────────────────
FILES_PER_PAGE = 6


def _trim(name: str, n: int = 32) -> str:
    return (name[:n - 1] + "…") if len(name) > n else name


# ── Build the picker keyboard ─────────────────────────────────
def build_picker_kb(gid: str, file_nodes: list, selected: set, page: int = 0) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(file_nodes) + FILES_PER_PAGE - 1) // FILES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start  = page * FILES_PER_PAGE
    slice_ = file_nodes[start: start + FILES_PER_PAGE]

    rows = []

    # ── File toggle rows ──────────────────────────────────────
    for i, f in enumerate(slice_):
        idx  = start + i
        fid  = f["id"]
        tick = "✅" if fid in selected else "⬜"
        lbl  = f"{tick} {_trim(f['name'])}  ({human_size(f['size'])})"
        rows.append([InlineKeyboardButton(lbl, callback_data=f"ms:{gid}:t:{idx}")])

    # ── Navigation row ─────────────────────────────────────────
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"ms:{gid}:p:{page - 1}"))
    nav.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ms:noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"ms:{gid}:p:{page + 1}"))
    rows.append(nav)

    # ── Select all / none row ─────────────────────────────────
    rows.append([
        InlineKeyboardButton("☑️ All",   callback_data=f"ms:{gid}:a"),
        InlineKeyboardButton("✖️ None",  callback_data=f"ms:{gid}:n"),
    ])

    # ── Action row ────────────────────────────────────────────
    selected_count = len(selected)
    start_label = f"✅ Download ({selected_count})" if selected_count else "✅ Download"
    rows.append([
        InlineKeyboardButton(start_label, callback_data=f"ms:{gid}:s"),
        InlineKeyboardButton("❌ Cancel",  callback_data=f"ms:{gid}:c"),
    ])

    return InlineKeyboardMarkup(rows)


def build_picker_text(gid: str, file_nodes: list, selected: set) -> str:
    total_size = sum(f["size"] for f in file_nodes if not f.get("is_dir"))
    sel_size   = sum(f["size"] for f in file_nodes if f["id"] in selected)
    return (
        "📂 <b>Mega Folder — Select Files</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Total: <b>{len(file_nodes)}</b> files  ({human_size(total_size)})\n"
        f"✅ Selected: <b>{len(selected)}</b> files"
        + (f"  ({human_size(sel_size)})" if sel_size else "") + "\n\n"
        "<i>Tap files to select/deselect, then tap Download.</i>"
    )


# ══════════════════════════════════════════════════════════════
#  Callback handler
# ══════════════════════════════════════════════════════════════
@Client.on_callback_query(filters.regex(r"^ms:"))
async def mega_picker_cb(client, cb):
    parts = cb.data.split(":")   # ms : gid : action [: param]
    if len(parts) < 3:
        return await cb.answer()

    gid    = parts[1]
    action = parts[2]
    param  = parts[3] if len(parts) > 3 else None

    if gid == "noop":
        return await cb.answer()

    # Look up state
    from bot.downloaders.mega_downloader import _mega_selections
    state = _mega_selections.get(gid)
    if not state:
        await cb.answer("⚠️ Session expired. Send the link again.", show_alert=True)
        try: await cb.message.delete()
        except Exception: pass
        return

    file_nodes = [f for f in state["file_list"] if not f.get("is_dir")]
    selected   = state.setdefault("selected_ids", set())
    page       = state.get("page", 0)

    # ── Actions ───────────────────────────────────────────────
    if action == "t":
        idx = int(param)
        if 0 <= idx < len(file_nodes):
            fid = file_nodes[idx]["id"]
            if fid in selected:
                selected.discard(fid)
            else:
                selected.add(fid)
        await cb.answer()

    elif action == "a":
        selected.update(f["id"] for f in file_nodes)
        await cb.answer(f"☑️ All {len(file_nodes)} files selected.")

    elif action == "n":
        selected.clear()
        await cb.answer("✖️ Selection cleared.")

    elif action == "p":
        page = int(param)
        state["page"] = page
        await cb.answer()

    elif action == "s":
        if not selected:
            return await cb.answer("⚠️ Select at least one file first.", show_alert=True)
        await cb.answer("⏳ Starting download…")
        # Write selected IDs to the store so resume_mega_with_selection can read them
        from web.mega_selection_store import write_state as _store_write
        file_list_meta = [{k: v for k, v in f.items() if k != "node"} for f in state["file_list"]]
        _store_write(gid, file_list_meta, list(selected))
        try:
            await cb.message.edit_text(
                f"⬇️ <b>Download starting…</b>\n"
                f"<b>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                f"✅ {len(selected)} file(s) selected\n\n"
                f"<i>Watch your task card for progress.</i>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        from bot.downloaders.mega_downloader import resume_mega_with_selection
        import asyncio
        asyncio.get_event_loop().create_task(resume_mega_with_selection(gid))
        return

    elif action == "c":
        await cb.answer("🚫 Cancelled.")
        try:
            await cb.message.edit_text(
                "🚫 <b>Mega download cancelled.</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        from bot.downloaders.mega_downloader import cancel_mega_selection
        import asyncio
        asyncio.get_event_loop().create_task(cancel_mega_selection(gid))
        return

    # Re-render picker
    state["page"] = page
    try:
        await cb.message.edit_text(
            build_picker_text(gid, file_nodes, selected),
            reply_markup=build_picker_kb(gid, file_nodes, selected, page),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass
