"""
tasks.py — stub for ENCODING-BOT queue tasks, rewritten to work within NXTL.

The original tasks.py handled queuing independently. In NXT_HUB, the queue
is managed by task_manager.py. This module only exposes queue_answer for
callbacks that reference it.
"""
import asyncio

_queue: asyncio.Queue = asyncio.Queue()
_active_count         = 0
MAX_CONCURRENT        = 2   # max simultaneous encodes


async def queue_answer(cb):
    """Reply to a callback that was queued."""
    pos = _queue.qsize()
    await cb.answer(
        f"⏳ Your task is queued (position {pos}). Please wait.",
        show_alert=True,
    )
