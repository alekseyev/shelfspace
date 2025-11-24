import asyncio
from contextlib import asynccontextmanager
from beanie import init_beanie
from pymongo import AsyncMongoClient

from shelfspace.models import beanie_models
from shelfspace.settings import settings


class AppCtx:
    mongo_client: AsyncMongoClient
    _initialized = False
    _init_event = asyncio.Event()

    @classmethod
    async def start(cls):
        if cls._initialized:
            return
        cls.mongo_client = AsyncMongoClient(settings.MONGO_URL)
        await init_beanie(
            database=cls.mongo_client[settings.MONGO_DB],
            document_models=beanie_models,
        )
        cls._initialized = True
        cls._init_event.set()

    @classmethod
    async def shutdown(cls):
        if cls._initialized:
            await cls.mongo_client.close()
            cls._initialized = False
            cls._init_event.clear()

    @classmethod
    async def ensure_initialized(cls):
        """Wait for initialization to complete if not already done."""
        if cls._initialized:
            return
        await cls._init_event.wait()


@asynccontextmanager
async def get_app_ctx(app=None):
    app_ctx = None
    if app:
        app_ctx = getattr(app.state, "app_ctx", None)

    if not app_ctx:
        app_ctx = AppCtx()
        await app_ctx.start()

        if app:
            app.state.app_ctx = app_ctx

    try:
        yield app_ctx
    finally:
        await app_ctx.shutdown()
