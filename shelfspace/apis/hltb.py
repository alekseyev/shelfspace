import re
from shelfspace.settings import settings
from shelfspace.browser import playwright_page
from shelfspace.utils import parse_date


class HowlongAPI:
    base_url = "https://howlongtobeat.com"
    BACKLOG_ROW_SELECTOR = "#user_games div[class*='table_row']"
    user = settings.HLTB_USER

    async def get_backlog(self):
        games = []

        async with playwright_page() as page:
            await page.goto(
                f"{self.base_url}/user/{self.user}/games/backlog",
                wait_until="domcontentloaded",
            )
            await page.wait_for_selector(self.BACKLOG_ROW_SELECTOR, timeout=5000)
            rows = await page.query_selector_all(self.BACKLOG_ROW_SELECTOR)

            for row in rows:
                title_el = await row.query_selector("a.text_blue")
                platform_el = await row.query_selector("span[class*='platform_alt']")

                title = (await title_el.inner_text()).strip() if title_el else None
                platform = (
                    (await platform_el.inner_text()).strip() if platform_el else None
                )
                href = await title_el.get_attribute("href") if title_el else None
                game_id = None
                if href:
                    m = re.search(r"/game/(\d+)", href)
                    if m:
                        game_id = int(m.group(1))

                if game_id:
                    games.append(
                        {
                            "title": title,
                            "platform": platform,
                            "hltb_id": game_id,
                            "url": f"{self.base_url}{href}" if href else None,
                        }
                    )

        return games

    async def get_game_data(self, hltb_id: int) -> dict:
        async with playwright_page() as page:
            await page.goto(
                f"{self.base_url}/game/{hltb_id}", wait_until="domcontentloaded"
            )

            # --- Title ---
            title_el = await page.query_selector(
                "div[class*='GameHeader'][class*='profile_header']"
            )
            title = await title_el.evaluate("el => el.firstChild.textContent.trim()")

            # --- Time: Main + Extras, Average, in minutes ---
            time_minutes = None
            rows = await page.query_selector_all(
                "table[class*='GameTimeTable'] tbody tr.spreadsheet"
            )

            for row in rows:
                first_cell = await row.query_selector("td:nth-child(1)")
                label = (await first_cell.inner_text()).strip() if first_cell else ""
                if (
                    label.lower() == "main story"
                    and not time_minutes
                    or label.lower() == "main + extras"
                ):
                    avg_cell = await row.query_selector("td:nth-child(3)")
                    if avg_cell:
                        text = (await avg_cell.inner_text()).strip()  # e.g., "39h 4m"
                        # convert to minutes
                        m = re.match(r"(?:(\d+)h)?\s*(?:(\d+)m)?", text)
                        if m:
                            hours = int(m.group(1)) if m.group(1) else 0
                            minutes = int(m.group(2)) if m.group(2) else 0
                            time_minutes = hours * 60 + minutes
                    break  # stop after finding the row

            # --- Rating ---
            rating = None
            rating_el = await page.query_selector("div[class*='GameReviewRoundUp'] h5")
            if rating_el:
                # Text like "74%\nRating"
                text = (await rating_el.inner_text()).strip()
                m = re.search(r"(\d+%)", text)
                if m:
                    rating = m.group(1)[:-1]

            # --- Release date ---
            dates = []

            info_blocks = await page.query_selector_all(
                "div[class*='GameSummary'][class*='profile_info']"
            )

            for block in info_blocks:
                strong = await block.query_selector("strong")
                if not strong:
                    continue

                label = (await strong.inner_text()).strip().lower()

                if label in {"na:", "eu:"}:
                    text = (await block.inner_text()).strip()
                    lines = [x.strip() for x in text.split("\n") if x.strip()]
                    if len(lines) >= 2:
                        date = parse_date(lines[1])
                        if date:
                            dates.append(date)

            release_date = min(dates).isoformat() if dates else None

            # --- Steam links ---
            steam_links = []
            store_buttons = await page.query_selector_all(
                "a[class*='StoreButton'][class*='steam']"
            )
            for btn in store_buttons:
                href = await btn.get_attribute("href")
                if href:
                    steam_links.append(href)

            return {
                "title": title,
                "time_to_beat": time_minutes,
                "rating": rating,
                "release_date": release_date,
                "steam_links": steam_links,
            }
