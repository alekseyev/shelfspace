from beanie import init_beanie
import typer

from shelfspace.apis.secrets import get_trakt_secrets, save_trakt_secrets
from shelfspace.apis.trakt import TraktAPI
from shelfspace.models import Entry
from shelfspace.utils import format_minutes

from async_typer import AsyncTyper
from pymongo import AsyncMongoClient

app = AsyncTyper()


async def init_db():
    mongo_client = AsyncMongoClient("mongodb://root:secret@localhost:4001")
    await init_beanie(
        database=mongo_client["shelfspace"],
        document_models=[Entry],
    )


async def add_new_entries(entries: list[Entry]):
    """
    Add new entries to the database if they don't exist.

    Args:
        entries: List of entries to add.

    Returns:
        None
    """
    for entry in entries:
        if await Entry.find_one(
            Entry.metadata["trakt_id"] == entry.metadata["trakt_id"]
        ):
            continue
        entry.shelf = "Icebox"
        typer.echo(f"Adding {entry.name} to Icebox")
        await entry.save()


@app.async_command()
async def process_movies():
    await init_db()
    typer.echo("Fetching Trakt data...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)
    entries = api.watchlist_movies()
    await add_new_entries(entries)

    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


def get_emoji_for_type(media_type):
    """Get emoji representation for media type."""
    emoji_map = {
        "Projects": "🏗️",
        "Duolingo": "🗣️",
        "Course": "📚",
        "Movie": "🎬",
        "Series": "📺",
        "Game": "🎮",
        "Game (VR)": "🥽",
        "Game (mobile)": "📱",
        "Book": "📖",
        "Book (educational)": "📚",
        "Book (comics)": "💭",
        "Article": "📰",
        "Talk/video": "🎥",
    }
    return emoji_map.get(media_type, "📌")


@app.async_command()
async def list_entries():
    await init_db()
    entries = await Entry.find().to_list()

    # Group entries by shelf
    entries_by_shelf = {}
    for entry in entries:
        shelf = entry.shelf or "Uncategorized"
        if shelf not in entries_by_shelf:
            entries_by_shelf[shelf] = []
        entries_by_shelf[shelf].append(entry)

    # Display entries grouped by shelf
    for shelf in sorted(entries_by_shelf.keys()):
        typer.echo(f"\n📚 {shelf}")
        shelf_entries = entries_by_shelf[shelf]

        for entry in sorted(shelf_entries, key=lambda e: e.name):
            emoji = get_emoji_for_type(entry.type)
            year = entry.release_date.year if entry.release_date else "N/A"
            estimated_formatted = (
                format_minutes(entry.estimated) if entry.estimated else "N/A"
            )
            typer.echo(
                f"  {emoji} \033[1m{entry.name}\033[0m ({year}) - {estimated_formatted}"
            )


if __name__ == "__main__":
    app()
