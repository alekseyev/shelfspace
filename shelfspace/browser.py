import json
from contextlib import asynccontextmanager

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class PlaywrightManager:
    _instance = None

    def __init__(self):
        self.playwright = None
        self.browser = None
        self._contexts: dict[str, object] = {}

    @classmethod
    def instance(cls):
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    async def _ensure_browser(self):
        if not self.playwright:
            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()

        if not self.browser:
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=["--headless=new"],
            )

    async def get_context(self, storage_state: dict | None = None):
        await self._ensure_browser()
        key = json.dumps(storage_state, sort_keys=True) if storage_state else ""
        if key not in self._contexts:
            self._contexts[key] = await self.browser.new_context(
                user_agent=_USER_AGENT,
                storage_state=storage_state,
            )
        return self._contexts[key]

    async def shutdown(self):
        for context in self._contexts.values():
            await context.close()
        self._contexts.clear()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


@asynccontextmanager
async def playwright_page(storage_state: dict | None = None):
    manager = PlaywrightManager.instance()
    context = await manager.get_context(storage_state=storage_state)
    page = await context.new_page()
    try:
        yield page
    finally:
        await page.close()
