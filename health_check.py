"""
Lightweight health-check server on port 8000.
Required for Koyeb and similar PaaS platforms.
"""
import asyncio
from aiohttp import web


async def _handle(_):
    return web.Response(text="OK")


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", _handle)
    app.router.add_get("/health", _handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    print("[health] Server running on :8000")
