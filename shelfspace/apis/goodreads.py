import csv
import re

from shelfspace.models import Entry, MediaType, Status
from shelfspace.esimations import (
    estimate_book_from_pages,
    estimate_comic_book_from_pages,
    estimate_ed_book_from_pages,
)

COMICS_PUBLISHERS_SUBSTRINGS = ["comix", "comic", "vovkulaka"]

INF = 1000000


def get_shelf_position(shelf_string: str, shelf: str) -> int:
    match = re.search(shelf + r" \(#(\d+)\)", shelf_string)
    if match:
        return int(match.group(1))
    else:
        return INF * 2


def get_books_from_csv(filename: str) -> list[Entry]:
    result = []
    with open(filename) as f:
        reader = csv.DictReader(f)
        for row in reader:
            shelf_string = row["Bookshelves with positions"]
            status = Status.CURRENT
            if row["Exclusive Shelf"] == "read":
                status = Status.DONE
                index = INF + get_shelf_position(shelf_string, "read")
            elif row["Exclusive Shelf"] == "to-read":
                status = Status.FUTURE
                index = 10 + get_shelf_position(shelf_string, "to-read")
            else:
                index = get_shelf_position(shelf_string, "reading")

            book_type = (
                MediaType.BOOK_ED
                if "want-to-read-tech" in row["Bookshelves"]
                else MediaType.BOOK
            )
            estimated = None
            is_audio = "audio" in row["Binding"].lower()

            if not is_audio and (pages := row["Number of Pages"]):
                publisher = row["Publisher"].lower()
                is_comics = any(
                    substring in publisher for substring in COMICS_PUBLISHERS_SUBSTRINGS
                )
                pages = int(pages)
                if is_comics:
                    estimated = estimate_comic_book_from_pages(pages)
                    book_type = MediaType.BOOK_COM
                elif book_type == MediaType.BOOK_ED:
                    estimated = estimate_ed_book_from_pages(pages)
                else:
                    estimated = estimate_book_from_pages(pages)

            result.append(
                (
                    index,
                    Entry(
                        type=book_type,
                        name=f"{row['Author']} - {row['Title']}",
                        status=status,
                        estimated=estimated,
                        spent=estimated if status == Status.DONE else None,
                        notes=f"GR: {row['Average Rating']} / 5",
                    ),
                )
            )

    result.sort(key=lambda x: x[0])

    return [book for _, book in result]
