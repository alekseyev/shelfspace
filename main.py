import logging

import typer

from shelfspace.apis.goodreads_old import get_books_from_csv_legacy
from shelfspace.apis.hltb import HowlongAPI
from shelfspace.apis.notion import NotionAPI
from shelfspace.cache import cached
from shelfspace.models import LegacyEntry, Status


logger = logging.getLogger(__name__)

app = typer.Typer()


@app.command()
def list_books(filename: str):
    books = get_books_from_csv_legacy(filename)
    for book in books:
        typer.echo(f"{book.name} - {book.estimated}h")


def add_new_entries_to_notion(entries: dict[str, LegacyEntry], auto: bool = False):
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

        confirm = auto or typer.confirm(f"Do you want to add {title} to Icebox?")
        if confirm:
            typer.echo(f"Adding {title}")
            typer.echo(notion.create_object(new_db_id, book))


@app.command()
@cached()
def process_books_csv(filename: str):
    books_in_csv = {book.name: book for book in get_books_from_csv_legacy(filename)}
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


if __name__ == "__main__":
    app()
