from datetime import date

import pytest
from beanie import init_beanie
from bson import ObjectId
from pymongo import AsyncMongoClient

from shelfspace.models import Entry, Shelf
from shelfspace.settings import settings

# Beanie refuses to build a Document without an initialised collection, so even
# tests that never touch the database need a connection. A scratch database
# keeps them clear of real data; nothing here writes to it.
TEST_DB = "shelfspace_test"


@pytest.fixture(autouse=True, scope="session")
async def _beanie():
    client = AsyncMongoClient(settings.MONGO_URL)
    await init_beanie(database=client[TEST_DB], document_models=[Entry, Shelf])
    yield
    await client.drop_database(TEST_DB)


def make_shelf(name: str, start: date | None = None, end: date | None = None) -> Shelf:
    shelf = Shelf(name=name, start_date=start, end_date=end)
    shelf.id = ObjectId()
    return shelf


@pytest.fixture
def shelves() -> dict[ObjectId, Shelf]:
    """Icebox and Backlog plus two consecutive week-long sprints."""
    built = [
        make_shelf("Icebox"),
        make_shelf("Backlog"),
        make_shelf("week1", date(2026, 8, 3), date(2026, 8, 9)),
        make_shelf("week2", date(2026, 8, 10), date(2026, 8, 16)),
    ]
    return {shelf.id: shelf for shelf in built}
