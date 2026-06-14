# Adapted from NEO-WZML (github.com/irisXDR/NEO-WZML)

from asyncio import sleep
from secrets import token_hex

from telegraph.aio import Telegraph
from telegraph.exceptions import RetryAfterError

from bot import LOGGER
import config


class TelegraphHelper:
    def __init__(self):
        self._telegraph   = Telegraph(domain="graph.org")
        self._author_name = getattr(config, "WATERMARK", "NXT_HUB")
        self._author_url  = f"https://t.me/{getattr(config, 'BOT_USERNAME', '')}"

    async def create_account(self):
        LOGGER.info("Creating Telegraph account…")
        try:
            await self._telegraph.create_account(
                short_name=token_hex(5),
                author_name=self._author_name,
                author_url=self._author_url,
            )
        except Exception as e:
            LOGGER.error(f"Telegraph account creation failed: {e}")

    async def create_page(self, title: str, content: str) -> dict:
        try:
            return await self._telegraph.create_page(
                title=title,
                author_name=self._author_name,
                author_url=self._author_url,
                html_content=content,
            )
        except RetryAfterError as st:
            LOGGER.warning(f"Telegraph flood control — sleeping {st.retry_after}s")
            await sleep(st.retry_after)
            return await self.create_page(title, content)

    async def edit_page(self, path: str, title: str, content: str) -> dict:
        try:
            return await self._telegraph.edit_page(
                path=path,
                title=title,
                author_name=self._author_name,
                author_url=self._author_url,
                html_content=content,
            )
        except RetryAfterError as st:
            LOGGER.warning(f"Telegraph flood control — sleeping {st.retry_after}s")
            await sleep(st.retry_after)
            return await self.edit_page(path, title, content)


telegraph = TelegraphHelper()
