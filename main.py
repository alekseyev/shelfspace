import logging

import typer

from shelfspace.apis.goodreads import get_books_from_csv
from shelfspace.apis.hltb import HowlongAPI
from shelfspace.apis.notion import NotionAPI
from shelfspace.apis.secrets import get_trakt_secrets, save_trakt_secrets
from shelfspace.apis.trakt import TraktAPI
from shelfspace.cache import cached
from shelfspace.models import Entry, Status


logger = logging.getLogger(__name__)

app = typer.Typer()


@app.command()
def list_books(filename: str):
    books = get_books_from_csv(filename)
    for book in books:
        typer.echo(f"{book.name} - {book.estimated}h")


def add_new_entries_to_notion(entries: dict[str, Entry], auto: bool = False):
    typer.echo("Initializing Notion API...")
    notion = NotionAPI()
    notion.get_databases()
    obj_count = notion.load_objects()
    new_db_id = notion.databases["Icebox"]
    typer.echo(f"Loaded {len(notion.databases)} databases with {obj_count} entries")

    entry_types = {entry.type for entry in entries.values()}
    entries_in_notion = [
        entry.name for entry in notion.get_objects_by_type(entry_types)
    ]

    typer.echo("Processing entries...")
    for title, book in entries.items():
        if book.status == Status.DONE:
            continue
        if title in entries_in_notion:
            typer.echo(f"{title} already in Notion!")
            continue
        confirm = auto or typer.confirm(f"Do you want to add {title} to Icebox?")
        if confirm:
            typer.echo(f"Adding {title}")
            typer.echo(notion.create_object(new_db_id, book))


@app.command()
@cached()
def process_books_csv(filename: str):
    books_in_csv = {book.name: book for book in get_books_from_csv(filename)}
    add_new_entries_to_notion(books_in_csv)


@app.command()
@cached()
def list_games():
    api = HowlongAPI()
    for game in api.get_games_list():
        if game.status != Status.DONE:
            typer.echo(f"{game.name} ({game.type}) {game.estimated} {game.notes}")


@app.command()
@cached()
def process_games(auto: bool = False):
    typer.echo("Fetching HLTB data...")
    api = HowlongAPI()
    entries = {entry.name: entry for entry in api.get_games_list()}
    add_new_entries_to_notion(entries, auto=auto)


@app.command()
@cached()
def list_movies():
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)
    for movie in api.watchlist_movies():
        typer.echo(f"{movie.name} ({movie.release_date}) - {movie.estimated}h")
    
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(
        access_token=api.access_token,
        refresh_token=api.refresh_token,
    )


@app.command()
@cached()
def process_movies(auto: bool = False):
    typer.echo("Fetching Trakt data...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)
    entries = {entry.name: entry for entry in api.watchlist_movies()}
    add_new_entries_to_notion(entries, auto=auto)
    
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.command()
@cached()
def list_shows():
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)
    for movie in api.watchlist_series():
        typer.echo(f"{movie.name} ({movie.type}) {movie.estimated}")
    
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.command()
@cached()
def process_shows(auto: bool = False):
    typer.echo("Fetching Trakt data...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)
    entries = {entry.name: entry for entry in api.watchlist_series()}
    add_new_entries_to_notion(entries, auto=auto)
    
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.command()
@cached()
def upcoming_episodes():
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)
    episodes = api.get_upcoming_episodes()
    
    # Group episodes by day
    episodes_by_day = {}
    for episode in episodes:
        day = episode['first_aired'][:10]  # Extract YYYY-MM-DD
        if day not in episodes_by_day:
            episodes_by_day[day] = []
        episodes_by_day[day].append(episode)
    
    # Display episodes grouped by day
    for day in sorted(episodes_by_day.keys()):
        typer.echo(f"\n📅 {day}")
        for episode in episodes_by_day[day]:
            typer.echo(
                f"  {episode['title']} - "
                f"S{episode['season']:02d}E{episode['episode']:02d} "
                f"({episode['runtime']}min)"
            )
    
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


if __name__ == "__main__":
    app()
