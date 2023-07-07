import typer

from shelfspace.apis.goodreads import get_books_from_csv
from shelfspace.apis.notion import NotionAPI
from shelfspace.models import MediaType, Status

app = typer.Typer()


@app.command()
def list_books(filename: str):
    books = get_books_from_csv(filename)
    for book in books:
        typer.echo(book.name, book.estimated)


@app.command()
def process_books_csv(filename: str):
    typer.echo("Initializing...")
    notion = NotionAPI()
    notion.get_databases()
    new_db_id = notion.databases["Icebox"]

    typer.echo("Fetching data...")
    books_in_notion = {
        book.name: book
        for book in notion.get_objects_by_type(
            [MediaType.BOOK, MediaType.BOOK_COM, MediaType.BOOK_COM]
        )
    }
    books_in_csv = {book.name: book for book in get_books_from_csv(filename)}

    typer.echo("Processing books...")
    for title, book in books_in_csv.items():
        if book.status == Status.DONE:
            continue
        if title in books_in_notion:
            typer.echo(f"{title} already in Notion!")
            continue
        typer.echo(f"Adding {title}")
        notion.create_object(new_db_id, book)


if __name__ == "__main__":
    app()
