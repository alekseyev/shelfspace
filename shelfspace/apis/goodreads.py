import re
from shelfspace.browser import playwright_page
from shelfspace.settings import settings
from shelfspace.utils import parse_date


class GoodreadsAPI:
    base_url = "https://www.goodreads.com"
    user = settings.GOODREADS_USER

    async def get_to_read(self):
        books = []

        async with playwright_page() as page:
            url = f"{self.base_url}/review/list/{self.user}?shelf=to-read&sort=position"
            while url:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                )
                await page.wait_for_selector("#booksBody", timeout=5000)
                rows = await page.query_selector_all("#booksBody tr")

                for row in rows:
                    # --- Position ---
                    pos_el = await row.query_selector("td.field.position div.value")
                    position = (
                        int((await pos_el.inner_text()).strip()) if pos_el else None
                    )

                    # --- Title ---
                    title_el = await row.query_selector("td.field.title div.value a")
                    title = (await title_el.inner_text()).strip() if title_el else None

                    # --- Book ID & URL ---
                    href = await title_el.get_attribute("href") if title_el else None
                    book_id = None
                    if href:
                        m = re.search(r"/book/show/(\d+)", href)
                        if m:
                            book_id = int(m.group(1))

                    # --- Rating ---
                    rating = None
                    rating_el = await row.query_selector(
                        "td.field.avg_rating div.value"
                    )
                    if rating_el:
                        rating_text = (await rating_el.inner_text()).strip()
                        try:
                            rating = float(rating_text)
                        except ValueError:
                            pass

                    if book_id:
                        books.append(
                            {
                                "title": title,
                                "goodreads_id": book_id,
                                "url": f"{self.base_url}{href}" if href else None,
                                "position": position,
                                "rating": rating,
                            }
                        )

                # Check for next page
                url = None
                next_el = await page.query_selector("a.next_page")
                if next_el:
                    next_href = await next_el.get_attribute("href")
                    if next_href:
                        url = f"{self.base_url}{next_href}"

        return books

    async def get_book_data(self, goodreads_id: int) -> dict:
        async with playwright_page() as page:
            await page.goto(
                f"{self.base_url}/book/show/{goodreads_id}",
                wait_until="domcontentloaded",
            )

            # --- Title ---
            title_el = await page.query_selector("#bookTitle")
            title = (await title_el.inner_text()).strip() if title_el else None

            # --- Authors ---
            authors = []

            contributors = await page.query_selector_all(
                "div.ContributorLinksList a.ContributorLink"
            )

            for contributor in contributors:
                role_el = await contributor.query_selector("span[data-testid='role']")
                role = (
                    (await role_el.inner_text()).strip().strip("()")
                    if role_el
                    else None
                )
                if role and role.lower() not in ("author", "writer"):
                    continue

                name_el = await contributor.query_selector("span[data-testid='name']")
                if not name_el:
                    continue

                name = (await name_el.inner_text()).strip()
                if name:
                    authors.append(name)

            # --- Page count ---
            page_count = None
            pages_el = await page.query_selector(
                "div.FeaturedDetails p[data-testid='pagesFormat']"
            )
            if pages_el:
                text = await pages_el.inner_text()  # e.g. "200 pages, Paperback"
                text = " ".join(text.split())  # normalize whitespace
                m = re.search(r"(\d+)\s+pages?", text)
                if m:
                    page_count = int(m.group(1))

            # --- Publication date ---
            publication_date = None
            pub_el = await page.query_selector(
                "div.FeaturedDetails p[data-testid='publicationInfo']"
            )
            if pub_el:
                text = (
                    await pub_el.inner_text()
                ).strip()  # e.g. "First published March 1, 2022"
                m = re.search(
                    r"(?:First\s+published|Published)\s+(.+)$", text, re.IGNORECASE
                )
                if m:
                    publication_date = m.group(1).strip()

            # --- Genres ---
            genres = []

            genre_els = await page.query_selector_all(
                "ul.CollapsableList a.Button--tag span.Button__labelItem"
            )

            for el in genre_els:
                genre = (await el.inner_text()).strip()
                if genre:
                    genres.append(genre.lower())

            return {
                "title": title,
                "author": ", ".join(authors),
                "page_count": page_count,
                "publication_date": parse_date(publication_date)
                if publication_date
                else None,
                "is_comics": "comics" in genres
                or "graphic novels" in genres
                or "manga" in genres,
                "is_educational": "programming" in genres
                or "computer science" in genres,
            }
