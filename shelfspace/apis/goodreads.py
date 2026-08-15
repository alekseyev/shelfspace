"""Reads the Goodreads to-read shelf via its public RSS feed.

The Goodreads API was retired in 2020, but the per-shelf RSS feeds are still
served and need no authentication, so long as the shelf is public. The feed
carries everything an entry needs (title, author, page count, rating), which
avoids scraping individual book pages.

Genre classification comes from the user's own Goodreads shelves rather than
community genre tags: shelve a book as `want-to-read-comics` or
`want-to-read-tech` and it is imported as the matching media type.
"""

import xml.etree.ElementTree as ET
from datetime import date

import aiohttp
from bson import ObjectId

from shelfspace.estimations import estimate_book
from shelfspace.models import Entry, MediaType, SubEntry
from shelfspace.settings import settings
from shelfspace.utils import format_minutes

TO_READ_SHELF = "to-read"
COMICS_SHELF = "want-to-read-comics"
EDUCATIONAL_SHELF = "want-to-read-tech"

# Each item carries a `user_shelves` list, but it is only as fresh as the feed
# it arrives in, and Goodreads caches every shelf's feed separately: the to-read
# feed can keep describing a book as plain to-read for days after it was also
# filed under a genre shelf. Genre membership is therefore read from the genre
# feeds themselves, where it is a fact about the response rather than a field
# inside it. Comics is checked first so a book on both shelves reads as comics.
GENRE_SHELVES = {
    COMICS_SHELF: MediaType.BOOK_COM,
    EDUCATIONAL_SHELF: MediaType.BOOK_ED,
}

# The feed serves 100 items per page; stop early rather than loop forever if
# Goodreads ever starts repeating pages instead of returning an empty one.
MAX_PAGES = 20

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _int_or_none(text: str | None) -> int | None:
    text = (text or "").strip()
    if not text.isdigit():
        return None
    return int(text)


def _float_or_none(text: str | None) -> float | None:
    text = (text or "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _rating(item: ET.Element) -> int | None:
    """Convert the feed's 0-5 community average to the 0-100 scale Entry uses."""
    average = _float_or_none(item.findtext("average_rating"))
    if not average:
        return None
    return int(average * 20)


def _parse_item(item: ET.Element) -> dict | None:
    goodreads_id = _int_or_none(item.findtext("book_id"))
    if not goodreads_id:
        return None

    shelves = [s.strip() for s in (item.findtext("user_shelves") or "").split(",")]
    shelves = [s for s in shelves if s]

    published_year = _int_or_none(item.findtext("book_published"))

    book = item.find("book")

    return {
        "goodreads_id": goodreads_id,
        "title": (item.findtext("title") or "").strip(),
        "author": (item.findtext("author_name") or "").strip(),
        "url": f"https://www.goodreads.com/book/show/{goodreads_id}",
        "rating": _rating(item),
        "page_count": _int_or_none(book.findtext("num_pages"))
        if book is not None
        else None,
        # The feed only reports a year, so the day and month are placeholders.
        "publication_date": date(published_year, 1, 1) if published_year else None,
        "shelves": shelves,
        # Filled in by get_to_read, from the genre feeds.
        "media_type": MediaType.BOOK,
    }


def build_entry(book: dict, shelf_id: ObjectId) -> Entry:
    """Create an Entry for a book seen on the to-read shelf for the first time."""
    return Entry(
        type=book["media_type"],
        name=f"{book['author']} - {book['title']}",
        subentries=[
            SubEntry(
                shelf_id=shelf_id,
                estimated=estimate_book(book["media_type"], book["page_count"]),
                release_date=book["publication_date"],
            )
        ],
        release_date=book["publication_date"],
        metadata={
            "goodreads_id": book["goodreads_id"],
            "goodreads_pages": book["page_count"],
        },
        links=[book["url"]],
        rating=book["rating"],
    )


def refresh_entry(entry: Entry, book: dict) -> list[str]:
    """Apply newly published feed data to an already imported book.

    Goodreads fills in details over time — a not-yet-released book gets its page
    count and firm release date late, the community average rating drifts, and
    re-shelving a book changes its type. Returns a description of every field
    changed, empty when nothing moved.

    Estimates are only recomputed while they still match what the last import
    derived, so a hand-tuned estimate is never overwritten.
    """
    changes = []

    old_type = MediaType(entry.type)
    new_type = book["media_type"]
    if new_type != old_type:
        entry.type = new_type
        changes.append(f"type {old_type.value} -> {new_type.value}")

    new_date = book["publication_date"]
    old_date = entry.release_date
    # The feed only reports a year, so an exact date captured by an earlier
    # import is better data. Only correct a genuinely different year.
    if new_date and (old_date is None or old_date.year != new_date.year):
        entry.release_date = new_date
        for subentry in entry.subentries:
            if subentry.release_date == old_date:
                subentry.release_date = new_date
        changes.append(
            f"release {old_date.year if old_date else '?'} -> {new_date.year}"
        )

    if book["rating"] is not None and book["rating"] != entry.rating:
        changes.append(f"rating {entry.rating or '?'} -> {book['rating']}")
        entry.rating = book["rating"]

    pages = book["page_count"]
    known_pages = entry.metadata.get("goodreads_pages")
    if pages and pages != known_pages:
        entry.metadata["goodreads_pages"] = pages
        changes.append(f"pages {known_pages or '?'} -> {pages}")

    unfinished = [s for s in entry.subentries if not s.is_finished]
    # The feed sometimes drops a page count it has already reported; the last one
    # it gave still beats leaving a re-shelved book on the wrong reading rate.
    new_estimate = estimate_book(new_type, pages or known_pages)
    # Splitting one book across several subentries is a manual decision, so
    # there is no safe way to redistribute a new estimate across them.
    if new_estimate and len(unfinished) == 1:
        subentry = unfinished[0]
        previous_estimate = estimate_book(old_type, known_pages)
        is_untouched = subentry.estimated in (None, previous_estimate)
        if is_untouched and subentry.estimated != new_estimate:
            subentry.estimated = new_estimate
            changes.append(f"estimate {format_minutes(new_estimate)}")

    return changes


class GoodreadsAPI:
    base_url = "https://www.goodreads.com"
    user = settings.GOODREADS_USER

    async def _fetch_page(
        self, session: aiohttp.ClientSession, shelf: str, page: int
    ) -> list[ET.Element]:
        url = f"{self.base_url}/review/list_rss/{self.user}"
        async with session.get(url, params={"shelf": shelf, "page": page}) as resp:
            resp.raise_for_status()
            body = await resp.text()

        channel = ET.fromstring(body).find("channel")
        if channel is None:
            return []
        return channel.findall("item")

    async def _fetch_shelf(
        self, session: aiohttp.ClientSession, shelf: str
    ) -> dict[int, dict]:
        """Every book on one shelf, in feed order, keyed by Goodreads id."""
        # Pages can overlap when the shelf changes mid-fetch, so key by id.
        books: dict[int, dict] = {}

        for page in range(1, MAX_PAGES + 1):
            items = await self._fetch_page(session, shelf, page)
            if not items:
                break

            new_on_page = 0
            for item in items:
                book = _parse_item(item)
                if not book:
                    continue
                if book["goodreads_id"] not in books:
                    new_on_page += 1
                books[book["goodreads_id"]] = book

            if not new_on_page:
                break

        return books

    async def get_to_read(self) -> list[dict]:
        """Return every book on the to-read shelf, in feed order."""
        if not self.user:
            raise ValueError("SET_GOODREADS_USER is not configured")

        async with aiohttp.ClientSession(
            headers={"User-Agent": _USER_AGENT}
        ) as session:
            books = await self._fetch_shelf(session, TO_READ_SHELF)

            genres: dict[int, MediaType] = {}
            for shelf, media_type in GENRE_SHELVES.items():
                for goodreads_id, book in (
                    await self._fetch_shelf(session, shelf)
                ).items():
                    genres.setdefault(goodreads_id, media_type)
                    # The genre feed can be the fresher of the two, so a book
                    # the to-read feed has not caught up on is taken from here.
                    if goodreads_id not in books and TO_READ_SHELF in book["shelves"]:
                        books[goodreads_id] = book

        for goodreads_id, book in books.items():
            book["media_type"] = genres.get(goodreads_id, MediaType.BOOK)

        return list(books.values())
