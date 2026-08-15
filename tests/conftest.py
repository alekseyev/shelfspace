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


def make_shelf(
    name: str,
    start: date | None = None,
    end: date | None = None,
    is_finished: bool = False,
) -> Shelf:
    shelf = Shelf(name=name, start_date=start, end_date=end, is_finished=is_finished)
    shelf.id = ObjectId()
    return shelf


@pytest.fixture
def shelves() -> dict[ObjectId, Shelf]:
    """Icebox and Backlog, a closed sprint, then two open week-long ones."""
    built = [
        make_shelf("Icebox"),
        make_shelf("Backlog"),
        make_shelf("past", date(2026, 7, 27), date(2026, 8, 2), is_finished=True),
        make_shelf("week1", date(2026, 8, 3), date(2026, 8, 9)),
        make_shelf("week2", date(2026, 8, 10), date(2026, 8, 16)),
    ]
    return {shelf.id: shelf for shelf in built}
