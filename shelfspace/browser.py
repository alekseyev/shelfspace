from contextlib import asynccontextmanager


class PlaywrightManager:
    _instance = None

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None

    @classmethod
    def instance(cls):
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    async def get_context(self):
        if not self.playwright:
            from playwright.async_api import async_playwright

            self.playwright = await async_playwright().start()

        if not self.browser:
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=["--headless=new"],
            )

        if not self.context:
            self.context = await self.browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )

        return self.context

    async def shutdown(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


@asynccontextmanager
async def playwright_page():
    manager = PlaywrightManager.instance()
    context = await manager.get_context()
    page = await context.new_page()
    try:
        yield page
    finally:
        await page.close()
