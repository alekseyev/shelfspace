from beanie import init_beanie
import typer

from shelfspace.apis.secrets import get_trakt_secrets, save_trakt_secrets
from shelfspace.apis.trakt import TraktAPI
from shelfspace.models import Entry, MediaType, SubEntry
from shelfspace.utils import format_minutes
from shelfspace.settings import settings

from async_typer import AsyncTyper
from pymongo import AsyncMongoClient

app = AsyncTyper()


async def init_db():
    mongo_client = AsyncMongoClient(settings.MONGO_URL)
    await init_beanie(
        database=mongo_client[settings.MONGO_DB],
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
    movies = api.watchlist_movies()
    for movie in movies:
        if await Entry.find_one(Entry.metadata["trakt_id"] == movie["trakt_id"]):
            continue
        entry = Entry(
            type=MediaType.MOVIE.value,
            name=movie["name"],
            subentries=[
                SubEntry(
                    shelf="Icebox",
                    estimated=movie["estimated"],
                    release_date=movie["release_date"],
                )
            ],
            release_date=movie["release_date"],
            metadata={"trakt_id": movie["trakt_id"]},
            links=[f"https://trakt.tv/movies/{movie['slug']}"],
            rating=movie["rating"],
        )
        typer.echo(f"Adding {entry.name} to Icebox")
        await entry.save()

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
        for subentry in entry.subentries:
            shelf = subentry.shelf or "Uncategorized"
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
                format_minutes(entry.subentries[0].estimated)
                if entry.subentries[0].estimated
                else "N/A"
            )
            typer.echo(
                f"  {emoji} \033[1m{entry.name}\033[0m ({year}) - {estimated_formatted}"
            )


if __name__ == "__main__":
    app()
