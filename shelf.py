from datetime import date, datetime, time, timedelta

from beanie import init_beanie
import typer

from shelfspace.apis.goodreads import GoodreadsAPI
from shelfspace.apis.hltb import HowlongAPI
from shelfspace.apis.secrets import get_trakt_secrets, save_trakt_secrets
from shelfspace.apis.trakt import TraktAPI
from shelfspace.estimations import (
    estimate_book_from_pages,
    estimate_comic_book_from_pages,
    estimate_ed_book_from_pages,
    round_up_game_estimate,
)
from shelfspace.models import Entry, MediaType, Shelf, SubEntry
from shelfspace.models import get_emoji_for_type
from shelfspace.utils import format_minutes
from shelfspace.settings import settings

from async_typer import AsyncTyper
from pymongo import AsyncMongoClient

app = AsyncTyper()


async def init_db():
    mongo_client = AsyncMongoClient(settings.MONGO_URL)
    await init_beanie(
        database=mongo_client[settings.MONGO_DB],
        document_models=[Entry, Shelf],
    )


def get_current_shelf_for_datetime(
    watched_dt: datetime, shelves_dict: dict[str, Shelf]
) -> Shelf:
    """Determine the current shelf for a given datetime.

    A shelf is considered current until 4:00 of the next calendar day after end_date.

    Args:
        watched_dt: The datetime when the item was watched (timezone-aware)
        shelves_dict: Dictionary of shelf_id -> Shelf

    Returns:
        The appropriate shelf for this datetime
    """
    # Get dated shelves (those with start and end dates)
    dated_shelves = [s for s in shelves_dict.values() if s.start_date and s.end_date]
    # Sort by start date
    dated_shelves.sort(key=lambda s: s.start_date)

    # Convert watched_dt to naive local time for comparison
    # (assuming shelves use local timezone)
    watched_naive = watched_dt.replace(tzinfo=None)

    # Check each dated shelf
    for shelf in dated_shelves:
        # Shelf is current from start_date 00:00 to end_date+1 04:00
        shelf_start = datetime.combine(shelf.start_date, time(0, 0))
        shelf_end = datetime.combine(shelf.end_date + timedelta(days=1), time(4, 0))

        if shelf_start <= watched_naive < shelf_end:
            return shelf

    # No matching dated shelf - use unfinished shelf with minimum weight
    unfinished_shelves = [s for s in shelves_dict.values() if not s.is_finished]
    return min(unfinished_shelves, key=lambda s: s.weight)


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
    icebox_shelf = await Shelf.find_one(Shelf.name == "Icebox")

    typer.echo("Fetching Trakt data...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)

    # Fetch movies from watchlist and maybe list
    typer.echo("Fetching watchlist movies...")
    watchlist_movies = api.get_movies()
    typer.echo("Fetching maybe list movies...")
    maybe_movies = api.get_movies("maybe") + api.get_movies("rewatch")

    # Combine and deduplicate by trakt_id
    movies_by_id = {m["trakt_id"]: m for m in watchlist_movies}
    for movie in maybe_movies:
        if movie["trakt_id"] not in movies_by_id:
            movies_by_id[movie["trakt_id"]] = movie

    typer.echo(f"Found {len(movies_by_id)} unique movies")

    for movie in movies_by_id.values():
        if await Entry.find_one(Entry.metadata["trakt_id"] == movie["trakt_id"]):
            continue

        # Fetch full movie details only when adding to database
        movie_data = api.get_movie_data(movie["trakt_id"])
        release_date = None
        if movie_data["release_date"]:
            release_date = datetime.fromisoformat(movie_data["release_date"])

        entry = Entry(
            type=MediaType.MOVIE.value,
            name=movie["name"],
            subentries=[
                SubEntry(
                    shelf_id=icebox_shelf.id,
                    estimated=movie_data["runtime"],
                    release_date=release_date,
                )
            ],
            release_date=release_date,
            metadata={"trakt_id": movie["trakt_id"]},
            links=[f"https://trakt.tv/movies/{movie['slug']}"],
            rating=int(movie_data["rating"]) if movie_data["rating"] else None,
        )
        typer.echo(f"Adding {entry.name} to Icebox")
        await entry.save()

    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.async_command()
async def process_shows():
    await init_db()
    icebox_shelf = await Shelf.find_one(Shelf.name == "Icebox")

    typer.echo("Fetching Trakt data...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)

    # Fetch shows from watchlist and maybe list
    typer.echo("Fetching watchlist shows...")
    watchlist_shows = api.get_shows()
    typer.echo("Fetching maybe list shows...")
    maybe_shows = api.get_shows("maybe")

    # Combine and deduplicate by trakt_id
    shows_by_id = {s["trakt_id"]: s for s in watchlist_shows}
    for show in maybe_shows:
        if show["trakt_id"] not in shows_by_id:
            shows_by_id[show["trakt_id"]] = show

    typer.echo(f"Found {len(shows_by_id)} unique shows")

    for show in shows_by_id.values():
        show_trakt_id = show["trakt_id"]

        # Check if show already has any entries in DB - skip API calls if so
        existing_entry = await Entry.find_one(
            Entry.metadata["show_trakt_id"] == show_trakt_id
        )
        if existing_entry:
            typer.echo(f"Skipping {show['name']} (already in DB)")
            continue

        typer.echo(f"Processing {show['name']}...")

        # First get season summary (cheap API call) to determine season count
        seasons_summary = api.get_seasons_summary(show_trakt_id)
        # Filter out specials (season 0)
        valid_seasons = [s for s in seasons_summary if s["number"]]
        is_multi_season = len(valid_seasons) > 1

        for season_info in valid_seasons:
            season_number = season_info["number"]
            season_trakt_id = f"{show_trakt_id}_s{season_number}"

            # Check if this specific season already exists
            if await Entry.find_one(Entry.metadata["trakt_id"] == season_trakt_id):
                continue

            # Only fetch episode data when we need to add the entry
            episodes = api.get_season_episodes(show_trakt_id, season_number)

            # Skip seasons with no episodes or no first_aired date
            if not episodes:
                continue
            if not episodes[0]["first_aired"]:
                continue

            # Build entry name: "Show Name" for single season, "Show Name S2" for multiple
            entry_name = (
                f"{show['name']} S{season_number}" if is_multi_season else show["name"]
            )

            # Build subentries for each episode
            subentries = []
            for episode in episodes:
                ep_number = episode["number"]
                ep_name = f"S{season_number:02d}E{ep_number:02d}"

                release_date = None
                if episode["first_aired"]:
                    # Parse ISO format and convert to date
                    aired_dt = datetime.fromisoformat(
                        episode["first_aired"].replace("Z", "+00:00")
                    )
                    release_date = aired_dt.date()

                subentries.append(
                    SubEntry(
                        shelf_id=icebox_shelf.id,
                        name=ep_name,
                        estimated=episode["runtime"],
                        release_date=release_date,
                    )
                )

            # Get first episode's release date for the entry
            entry_release_date = subentries[0].release_date if subentries else None

            entry = Entry(
                type=MediaType.SERIES.value,
                name=entry_name,
                subentries=subentries,
                release_date=entry_release_date,
                metadata={
                    "trakt_id": season_trakt_id,
                    "show_trakt_id": show_trakt_id,
                },
                links=[
                    f"https://trakt.tv/shows/{show['slug']}/seasons/{season_number}"
                ],
            )
            typer.echo(f"Adding {entry.name} ({len(subentries)} episodes) to Icebox")
            await entry.save()

    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.async_command()
async def process_upcoming(days: int = 49):
    """Add upcoming episodes from Trakt calendar that aren't in DB yet.

    Episodes are placed into shelves based on their air date:
    - If air date falls within a shelf's date range, episode goes there
    - If show has older episodes in "Icebox", new episodes go to "Icebox"
    - Otherwise episodes go to "Backlog"

    Also updates existing episodes in "Backlog" to move them to appropriate dated shelves.
    """
    await init_db()

    # Load all active shelves
    shelves_dict = await Shelf.get_shelves_dict()

    # Get icebox and backlog shelves from dict
    icebox_shelf = next(s for s in shelves_dict.values() if s.name == "Icebox")
    backlog_shelf = next(s for s in shelves_dict.values() if s.name == "Backlog")

    # Get dated shelves (those with start and end dates)
    dated_shelves = [s for s in shelves_dict.values() if s.start_date and s.end_date]
    # Sort by start date for efficient lookup
    dated_shelves.sort(key=lambda s: s.start_date)

    def find_shelf_for_date(air_date: date | None, has_icebox: bool) -> str:
        """Find appropriate shelf for an episode based on air date.

        Returns shelf_id.
        """
        if not air_date:
            # No air date - use default logic
            return icebox_shelf.id if has_icebox else backlog_shelf.id

        # Check if air date falls within any dated shelf
        for shelf in dated_shelves:
            if shelf.start_date <= air_date <= shelf.end_date:
                return shelf.id

        # No matching dated shelf - use default logic
        return icebox_shelf.id if has_icebox else backlog_shelf.id

    typer.echo("Fetching upcoming episodes from Trakt...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)

    upcoming = api.get_upcoming_episodes(days)
    typer.echo(f"Found {len(upcoming)} upcoming episodes")

    # Group episodes by show+season for efficient processing
    episodes_by_season: dict[str, list[dict]] = {}
    for ep in upcoming:
        season_key = f"{ep['show_trakt_id']}_s{ep['season']}"
        if season_key not in episodes_by_season:
            episodes_by_season[season_key] = {
                "show_title": ep["title"],
                "show_trakt_id": ep["show_trakt_id"],
                "show_slug": ep["show_slug"],
                "season": ep["season"],
                "episodes": [],
            }
        episodes_by_season[season_key]["episodes"].append(ep)

    added_count = 0
    updated_count = 0
    for season_key, season_data in episodes_by_season.items():
        show_trakt_id = season_data["show_trakt_id"]
        season_number = season_data["season"]
        show_title = season_data["show_title"]
        show_slug = season_data["show_slug"]

        # Check if entry for this season exists
        existing_entry = await Entry.find_one(Entry.metadata["trakt_id"] == season_key)

        if existing_entry:
            # Check if any existing subentry is in Icebox
            has_icebox = any(
                sub.shelf_id == icebox_shelf.id for sub in existing_entry.subentries
            )

            # Find existing episode numbers
            existing_ep_names = {sub.name for sub in existing_entry.subentries}

            # Update existing episodes in Backlog to appropriate dated shelves
            for sub in existing_entry.subentries:
                if sub.shelf_id == backlog_shelf.id and sub.release_date:
                    new_shelf_id = find_shelf_for_date(sub.release_date, has_icebox)
                    if new_shelf_id != backlog_shelf.id:
                        sub.shelf_id = new_shelf_id
                        new_shelf_name = shelves_dict[new_shelf_id].name
                        typer.echo(
                            f"Moving {show_title} {sub.name} from Backlog to {new_shelf_name}"
                        )
                        updated_count += 1

            # Add missing episodes
            entry_added_count = 0
            for ep in season_data["episodes"]:
                ep_name = f"S{season_number:02d}E{ep['episode']:02d}"
                if ep_name in existing_ep_names:
                    continue

                release_date = None
                if ep["first_aired"]:
                    aired_dt = datetime.fromisoformat(
                        ep["first_aired"].replace("Z", "+00:00")
                    )
                    release_date = aired_dt.date()

                # Find appropriate shelf based on air date
                shelf_id = find_shelf_for_date(release_date, has_icebox)
                shelf_name = shelves_dict[shelf_id].name

                new_subentry = SubEntry(
                    shelf_id=shelf_id,
                    name=ep_name,
                    estimated=ep["runtime"],
                    release_date=release_date,
                )
                existing_entry.subentries.append(new_subentry)
                typer.echo(f"Adding {show_title} {ep_name} to {shelf_name}")
                entry_added_count += 1
                added_count += 1

            if entry_added_count > 0 or updated_count > 0:
                await existing_entry.save()
        else:
            # Entry doesn't exist - check if show has ANY season in Icebox
            any_icebox_entry = await Entry.find_one(
                {
                    "metadata.show_trakt_id": show_trakt_id,
                    "subentries.shelf_id": icebox_shelf.id,
                }
            )
            has_icebox = bool(any_icebox_entry)

            # Get season summary to determine if multi-season
            seasons_summary = api.get_seasons_summary(show_trakt_id)
            valid_seasons = [s for s in seasons_summary if s["number"]]
            is_multi_season = len(valid_seasons) > 1

            entry_name = (
                f"{show_title} S{season_number}" if is_multi_season else show_title
            )

            # Build subentries for the upcoming episodes
            subentries = []
            shelves_used = set()
            for ep in season_data["episodes"]:
                ep_name = f"S{season_number:02d}E{ep['episode']:02d}"
                release_date = None
                if ep["first_aired"]:
                    aired_dt = datetime.fromisoformat(
                        ep["first_aired"].replace("Z", "+00:00")
                    )
                    release_date = aired_dt.date()

                # Find appropriate shelf based on air date
                shelf_id = find_shelf_for_date(release_date, has_icebox)
                shelves_used.add(shelves_dict[shelf_id].name)

                subentries.append(
                    SubEntry(
                        shelf_id=shelf_id,
                        name=ep_name,
                        estimated=ep["runtime"],
                        release_date=release_date,
                    )
                )

            # Get first episode's release date for the entry
            entry_release_date = subentries[0].release_date if subentries else None

            entry = Entry(
                type=MediaType.SERIES.value,
                name=entry_name,
                subentries=subentries,
                release_date=entry_release_date,
                metadata={
                    "trakt_id": season_key,
                    "show_trakt_id": show_trakt_id,
                },
                links=[f"https://trakt.tv/shows/{show_slug}/seasons/{season_number}"],
            )
            shelves_summary = ", ".join(sorted(shelves_used))
            typer.echo(
                f"Adding {entry_name} ({len(subentries)} episodes) to {shelves_summary}"
            )
            await entry.save()
            added_count += len(subentries)

    typer.echo(
        f"Added {added_count} new episodes, updated {updated_count} existing episodes"
    )
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.async_command()
async def list_entries():
    await init_db()
    entries = await Entry.find().to_list()
    await Shelf.get_shelves_dict()

    # Group entries by shelf (store entry with its relevant subentries for that shelf)
    entries_by_shelf: dict[str, list[tuple[Entry, list[SubEntry]]]] = {}
    for entry in entries:
        # Group subentries by shelf
        subentries_by_shelf: dict[str, list[SubEntry]] = {}
        for subentry in entry.subentries:
            shelf = subentry.shelf_name
            if shelf not in subentries_by_shelf:
                subentries_by_shelf[shelf] = []
            subentries_by_shelf[shelf].append(subentry)

        # Add entry to each shelf it appears in
        for shelf, subentries in subentries_by_shelf.items():
            if shelf not in entries_by_shelf:
                entries_by_shelf[shelf] = []
            entries_by_shelf[shelf].append((entry, subentries))

    # Display entries grouped by shelf
    for shelf in sorted(entries_by_shelf.keys()):
        typer.echo(f"\n📚 {shelf}")
        shelf_entries = entries_by_shelf[shelf]

        for entry, subentries in sorted(shelf_entries, key=lambda x: x[0].name):
            emoji = get_emoji_for_type(entry.type)
            year = entry.release_date.year if entry.release_date else "N/A"

            if len(subentries) == 1:
                # Single subentry: show on one line
                sub = subentries[0]
                estimated_formatted = (
                    format_minutes(sub.estimated) if sub.estimated else "N/A"
                )
                typer.echo(
                    f"  {emoji} \033[1m{entry.name}\033[0m ({year}) - {estimated_formatted}"
                )
            else:
                # Multiple subentries: show entry header then list subentries
                total_estimated = sum(s.estimated or 0 for s in subentries)
                total_formatted = (
                    format_minutes(total_estimated) if total_estimated else "N/A"
                )
                typer.echo(
                    f"  {emoji} \033[1m{entry.name}\033[0m ({year}) - {total_formatted} total"
                )
                for sub in subentries:
                    sub_name = sub.name or "(unnamed)"
                    sub_estimated = (
                        format_minutes(sub.estimated) if sub.estimated else "N/A"
                    )
                    typer.echo(f"      └─ {sub_name}: {sub_estimated}")


@app.async_command()
async def process_games():
    await init_db()
    icebox_shelf = await Shelf.find_one(Shelf.name == "Icebox")

    typer.echo("Fetching HLTB data...")
    api = HowlongAPI()
    games = await api.get_backlog()
    typer.echo(f"Found {len(games)} games")
    for game in games:
        if await Entry.find_one(Entry.metadata["hltb_id"] == game["hltb_id"]):
            continue

        game_data = await api.get_game_data(game["hltb_id"])

        game_type = MediaType.GAME
        if game["platform"] == "Mobile":
            game_type = MediaType.GAME_MOBILE
        elif game["platform"] in ("PC VR", "Meta Quest"):
            game_type = MediaType.GAME_VR

        entry = Entry(
            type=game_type.value,
            name=game["title"],
            subentries=[
                SubEntry(
                    shelf_id=icebox_shelf.id,
                    estimated=round_up_game_estimate(game_data["time_to_beat"])
                    if game_data["time_to_beat"]
                    else None,
                    release_date=game_data["release_date"],
                )
            ],
            release_date=game_data["release_date"],
            metadata={"hltb_id": game["hltb_id"]},
            rating=game_data["rating"],
            links=[game["url"]] + game_data["steam_links"],
        )

        typer.echo(f"Adding {entry.name} ({entry.type.value}) to Icebox")
        await entry.save()


@app.async_command()
async def process_books():
    await init_db()
    icebox_shelf = await Shelf.find_one(Shelf.name == "Icebox")
    backlog_shelf = await Shelf.find_one(Shelf.name == "Backlog")

    typer.echo("Fetching Goodreads data...")
    api = GoodreadsAPI()
    books = await api.get_to_read()
    for book in books:
        if await Entry.find_one(Entry.metadata["goodreads_id"] == book["goodreads_id"]):
            continue

        book_data = await api.get_book_data(book["goodreads_id"])
        media_type = MediaType.BOOK
        pages = book_data.get("page_count", 0)
        estimated = estimate_book_from_pages(pages) if pages else None
        if book_data.get("is_comics"):
            media_type = MediaType.BOOK_COM
            estimated = estimate_comic_book_from_pages(pages) if pages else None
        elif book_data.get("is_educational"):
            media_type = MediaType.BOOK_ED
            estimated = estimate_ed_book_from_pages(pages) if pages else None

        shelf_id = icebox_shelf.id
        shelf_name = "Icebox"
        if book["position"] < 35:
            shelf_id = backlog_shelf.id
            shelf_name = "Backlog"

        entry = Entry(
            type=media_type.value,
            name=f"{book_data['author']} - {book['title']}",
            subentries=[
                SubEntry(
                    shelf_id=shelf_id,
                    estimated=estimated,
                    release_date=book_data.get("publication_date"),
                )
            ],
            release_date=book_data.get("publication_date"),
            metadata={"goodreads_id": book["goodreads_id"]},
            links=[f"https://www.goodreads.com/book/show/{book['goodreads_id']}"],
            rating=int(book["rating"] * 20) if book["rating"] else None,
        )
        typer.echo(
            f"Adding {get_emoji_for_type(entry.type)} {entry.name} to {shelf_name}"
        )
        await entry.save()


@app.async_command()
async def update_trakt_lists(limit: int = 10):
    """Remove recently watched items from Trakt 'maybe' and 'rewatch' lists.

    Checks recent watch history and removes those items from the specified lists
    to keep them clean.

    Args:
        limit: Number of recent watch history items to check (default: 50)
    """
    typer.echo("Fetching watch history from Trakt...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)

    history = api.get_watch_history(limit)
    typer.echo(f"Found {len(history)} recently watched items")

    # Collect unique movie and show IDs from watch history
    watched_movie_ids = set()
    watched_show_ids = set()

    for item in history:
        if item["type"] == "movie":
            watched_movie_ids.add(item["trakt_id"])
        elif item["type"] == "episode":
            watched_show_ids.add(item["show_trakt_id"])

    typer.echo(
        f"Found {len(watched_movie_ids)} unique movies, {len(watched_show_ids)} unique shows"
    )

    # Remove from both lists
    lists_to_clean = ["maybe", "rewatch"]
    for list_slug in lists_to_clean:
        if watched_movie_ids or watched_show_ids:
            typer.echo(f"Removing items from '{list_slug}' list...")
            try:
                result = api.remove_from_list(
                    list_slug,
                    movies=list(watched_movie_ids) if watched_movie_ids else None,
                    shows=list(watched_show_ids) if watched_show_ids else None,
                )
                deleted = result.get("deleted", {})
                movies_deleted = deleted.get("movies", 0)
                shows_deleted = deleted.get("shows", 0)
                typer.echo(
                    f"  ✓ Removed {movies_deleted} movies, {shows_deleted} shows from '{list_slug}'"
                )
            except Exception as e:
                typer.echo(f"  ✗ Error updating '{list_slug}': {e}")

    typer.echo("List cleanup complete")
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


@app.async_command()
async def process_watched(limit: int = 10):
    """Sync recently watched movies and episodes from Trakt.

    Checks the last N watched items and updates the database:
    - Creates entries if they don't exist
    - Moves subentries to the appropriate shelf based on watch time
    - Marks subentries as finished with spent=estimated
    - Creates new subentries if rewatching (previous in finished shelf)

    Args:
        limit: Number of recent watch history items to process (default: 5)
    """
    await init_db()

    # Load all shelves
    shelves_dict = await Shelf.get_shelves_dict()
    icebox_shelf = next(s for s in shelves_dict.values() if s.name == "Icebox")

    typer.echo("Fetching watch history from Trakt...")
    secrets = get_trakt_secrets()
    api = TraktAPI(**secrets)

    history = api.get_watch_history(limit)
    typer.echo(f"Found {len(history)} recently watched items")

    for item in history:
        # Parse watched_at timestamp (ISO format with Z)
        watched_dt = datetime.fromisoformat(item["watched_at"].replace("Z", "+00:00"))
        # Determine the appropriate shelf for this watch time
        target_shelf = get_current_shelf_for_datetime(watched_dt, shelves_dict)

        if item["type"] == "movie":
            await _process_watched_movie(
                item, api, target_shelf, icebox_shelf, shelves_dict
            )
        elif item["type"] == "episode":
            await _process_watched_episode(
                item, api, target_shelf, icebox_shelf, shelves_dict
            )

    typer.echo("Watch history sync complete")
    # Save tokens (in case they were refreshed during API calls)
    save_trakt_secrets(**api._get_tokens())


async def _process_watched_movie(
    item: dict,
    api: TraktAPI,
    target_shelf: Shelf,
    icebox_shelf: Shelf,
    shelves_dict: dict[str, Shelf],
):
    """Process a watched movie from history."""
    trakt_id = item["trakt_id"]

    # Find existing entry
    entry = await Entry.find_one(Entry.metadata["trakt_id"] == trakt_id)

    if not entry:
        # Create new entry
        movie_data = api.get_movie_data(trakt_id)
        release_date = None
        if movie_data["release_date"]:
            release_date = datetime.fromisoformat(movie_data["release_date"])

        entry = Entry(
            type=MediaType.MOVIE.value,
            name=item["title"],
            subentries=[
                SubEntry(
                    shelf_id=target_shelf.id,
                    estimated=movie_data["runtime"],
                    spent=movie_data["runtime"],
                    is_finished=True,
                    release_date=release_date,
                )
            ],
            release_date=release_date,
            metadata={"trakt_id": trakt_id},
            links=[f"https://trakt.tv/movies/{item['slug']}"],
            rating=int(movie_data["rating"]) if movie_data["rating"] else None,
        )
        typer.echo(f"✓ Created movie '{entry.name}' in {target_shelf.name} (watched)")
        await entry.save()
        return

    # Entry exists - check if there's an unfinished subentry or if we need to create a new one
    unfinished_sub = None
    finished_shelf_ids = {s.id for s in shelves_dict.values() if s.is_finished}

    for sub in entry.subentries:
        if not sub.is_finished:
            unfinished_sub = sub
            break

    if unfinished_sub:
        # Update existing unfinished subentry
        changed = False
        if unfinished_sub.shelf_id != target_shelf.id:
            old_shelf_name = shelves_dict[unfinished_sub.shelf_id].name
            unfinished_sub.shelf_id = target_shelf.id
            typer.echo(
                f"✓ Moved '{entry.name}' from {old_shelf_name} to {target_shelf.name}"
            )
            changed = True

        if not unfinished_sub.is_finished:
            unfinished_sub.is_finished = True
            if unfinished_sub.estimated:
                unfinished_sub.spent = unfinished_sub.estimated
            typer.echo(f"✓ Marked '{entry.name}' as finished")
            changed = True

        if changed:
            await entry.save()
    else:
        # All subentries are finished - check if any are in non-finished shelves
        # If so, it's already been processed - skip it
        has_finished_in_active_shelf = any(
            sub.is_finished and sub.shelf_id not in finished_shelf_ids
            for sub in entry.subentries
        )

        if has_finished_in_active_shelf:
            # Already processed, skip
            return

        # All subentries are in finished shelves - create a new one (rewatch)
        movie_data = api.get_movie_data(trakt_id)
        new_sub = SubEntry(
            shelf_id=target_shelf.id,
            estimated=movie_data["runtime"],
            spent=movie_data["runtime"],
            is_finished=True,
        )
        entry.subentries.append(new_sub)
        typer.echo(
            f"✓ Created new subentry for '{entry.name}' in {target_shelf.name} (rewatch)"
        )
        await entry.save()


async def _process_watched_episode(
    item: dict,
    api: TraktAPI,
    target_shelf: Shelf,
    icebox_shelf: Shelf,
    shelves_dict: dict[str, Shelf],
):
    """Process a watched episode from history."""
    show_trakt_id = item["show_trakt_id"]
    season_number = item["season"]
    episode_number = item["episode"]
    season_key = f"{show_trakt_id}_s{season_number}"
    ep_name = f"S{season_number:02d}E{episode_number:02d}"

    # Find existing entry for this season
    entry = await Entry.find_one(Entry.metadata["trakt_id"] == season_key)

    if not entry:
        # Entry doesn't exist - create it
        # Get season summary to determine if multi-season
        seasons_summary = api.get_seasons_summary(show_trakt_id)
        valid_seasons = [s for s in seasons_summary if s["number"]]
        is_multi_season = len(valid_seasons) > 1

        entry_name = (
            f"{item['show_title']} S{season_number}"
            if is_multi_season
            else item["show_title"]
        )

        # Fetch episode data
        episode_data = api.get_episode_data(
            show_trakt_id, season_number, episode_number
        )

        release_date = None
        if episode_data["first_aired"]:
            aired_dt = datetime.fromisoformat(
                episode_data["first_aired"].replace("Z", "+00:00")
            )
            release_date = aired_dt.date()

        entry = Entry(
            type=MediaType.SERIES.value,
            name=entry_name,
            subentries=[
                SubEntry(
                    shelf_id=target_shelf.id,
                    name=ep_name,
                    estimated=episode_data["runtime"],
                    spent=episode_data["runtime"],
                    is_finished=True,
                    release_date=release_date,
                )
            ],
            release_date=release_date,
            metadata={
                "trakt_id": season_key,
                "show_trakt_id": show_trakt_id,
            },
            links=[
                f"https://trakt.tv/shows/{item['show_slug']}/seasons/{season_number}"
            ],
        )
        typer.echo(
            f"✓ Created episode '{entry.name} {ep_name}' in {target_shelf.name} (watched)"
        )
        await entry.save()
        return

    # Entry exists - find the specific episode subentry
    episode_sub = None
    for sub in entry.subentries:
        if sub.name == ep_name:
            episode_sub = sub
            break

    finished_shelf_ids = {s.id for s in shelves_dict.values() if s.is_finished}

    if not episode_sub:
        # Episode doesn't exist - create it
        episode_data = api.get_episode_data(
            show_trakt_id, season_number, episode_number
        )
        release_date = None
        if episode_data["first_aired"]:
            aired_dt = datetime.fromisoformat(
                episode_data["first_aired"].replace("Z", "+00:00")
            )
            release_date = aired_dt.date()

        new_sub = SubEntry(
            shelf_id=target_shelf.id,
            name=ep_name,
            estimated=episode_data["runtime"],
            spent=episode_data["runtime"],
            is_finished=True,
            release_date=release_date,
        )
        entry.subentries.append(new_sub)
        typer.echo(
            f"✓ Created episode '{entry.name} {ep_name}' in {target_shelf.name} (watched)"
        )
        await entry.save()
        return

    # Episode exists - check if finished and in finished shelf
    if episode_sub.is_finished and episode_sub.shelf_id in finished_shelf_ids:
        # Create new subentry (rewatch)
        episode_data = api.get_episode_data(
            show_trakt_id, season_number, episode_number
        )
        new_sub = SubEntry(
            shelf_id=target_shelf.id,
            name=ep_name,
            estimated=episode_data["runtime"],
            spent=episode_data["runtime"],
            is_finished=True,
            release_date=episode_sub.release_date,
        )
        entry.subentries.append(new_sub)
        typer.echo(
            f"✓ Created new subentry for '{entry.name} {ep_name}' in {target_shelf.name} (rewatch)"
        )
        await entry.save()
        return

    # Episode is already finished in an active shelf - skip
    if episode_sub.is_finished and episode_sub.shelf_id not in finished_shelf_ids:
        return

    # Update existing unfinished episode
    changed = False
    if episode_sub.shelf_id != target_shelf.id:
        old_shelf_name = shelves_dict[episode_sub.shelf_id].name
        episode_sub.shelf_id = target_shelf.id
        typer.echo(
            f"✓ Moved '{entry.name} {ep_name}' from {old_shelf_name} to {target_shelf.name}"
        )
        changed = True

    if not episode_sub.is_finished:
        episode_sub.is_finished = True
        if episode_sub.estimated:
            episode_sub.spent = episode_sub.estimated
        typer.echo(f"✓ Marked '{entry.name} {ep_name}' as finished")
        changed = True

    if changed:
        await entry.save()


if __name__ == "__main__":
    app()
