from datetime import datetime

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

    # Fetch movies from watchlist and maybe list
    typer.echo("Fetching watchlist movies...")
    watchlist_movies = api.get_movies()
    typer.echo("Fetching maybe list movies...")
    maybe_movies = api.get_movies("maybe")

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
                    shelf="Icebox",
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
                        shelf="Icebox",
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

    By default, new episodes go to "Backlog" shelf.
    If the show has older episodes in "Icebox", new episodes go to "Icebox" instead.
    """
    await init_db()
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
    for season_key, season_data in episodes_by_season.items():
        show_trakt_id = season_data["show_trakt_id"]
        season_number = season_data["season"]
        show_title = season_data["show_title"]
        show_slug = season_data["show_slug"]

        # Check if entry for this season exists
        existing_entry = await Entry.find_one(Entry.metadata["trakt_id"] == season_key)

        if existing_entry:
            # Check if any existing subentry is in Icebox
            has_icebox = any(sub.shelf == "Icebox" for sub in existing_entry.subentries)
            default_shelf = "Icebox" if has_icebox else "Backlog"

            # Find existing episode numbers
            existing_ep_names = {sub.name for sub in existing_entry.subentries}

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

                new_subentry = SubEntry(
                    shelf=default_shelf,
                    name=ep_name,
                    estimated=ep["runtime"],
                    release_date=release_date,
                )
                existing_entry.subentries.append(new_subentry)
                typer.echo(f"Adding {show_title} {ep_name} to {default_shelf}")
                entry_added_count += 1
                added_count += 1

            if entry_added_count > 0:
                await existing_entry.save()
        else:
            # Entry doesn't exist - check if show has ANY season in Icebox
            any_icebox_entry = await Entry.find_one(
                {
                    "metadata.show_trakt_id": show_trakt_id,
                    "subentries.shelf": "Icebox",
                }
            )
            default_shelf = "Icebox" if any_icebox_entry else "Backlog"

            # Get season summary to determine if multi-season
            seasons_summary = api.get_seasons_summary(show_trakt_id)
            valid_seasons = [s for s in seasons_summary if s["number"]]
            is_multi_season = len(valid_seasons) > 1

            entry_name = (
                f"{show_title} S{season_number}" if is_multi_season else show_title
            )

            # Build subentries for the upcoming episodes
            subentries = []
            for ep in season_data["episodes"]:
                ep_name = f"S{season_number:02d}E{ep['episode']:02d}"
                release_date = None
                if ep["first_aired"]:
                    aired_dt = datetime.fromisoformat(
                        ep["first_aired"].replace("Z", "+00:00")
                    )
                    release_date = aired_dt.date()

                subentries.append(
                    SubEntry(
                        shelf=default_shelf,
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
            typer.echo(
                f"Adding {entry_name} ({len(subentries)} episodes) to {default_shelf}"
            )
            await entry.save()
            added_count += len(subentries)

    typer.echo(f"Added {added_count} new episodes")
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

    # Group entries by shelf (store entry with its relevant subentries for that shelf)
    entries_by_shelf: dict[str, list[tuple[Entry, list[SubEntry]]]] = {}
    for entry in entries:
        # Group subentries by shelf
        subentries_by_shelf: dict[str, list[SubEntry]] = {}
        for subentry in entry.subentries:
            shelf = subentry.shelf or "Uncategorized"
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
    typer.echo("Fetching HLTB data...")
    api = HowlongAPI()
    games = await api.get_backlog()
    typer.echo(f"Found {len(games)} games")
    for game in games:
        if await Entry.find_one(Entry.metadata["hltb_id"] == game["hltb_id"]):
            continue

        game_data = await api.get_game_data(game["hltb_id"])
        shelf = "Icebox"

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
                    shelf=shelf,
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

        typer.echo(f"Adding {entry.name} ({entry.type.value}) to {shelf}")
        await entry.save()


@app.async_command()
async def process_books():
    await init_db()
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

        shelf = "Icebox"
        if book["position"] < 35:
            shelf = "Backlog"
        entry = Entry(
            type=media_type.value,
            name=f"{book_data['author']} - {book['title']}",
            subentries=[
                SubEntry(
                    shelf=shelf,
                    estimated=estimated,
                    release_date=book_data.get("publication_date"),
                )
            ],
            release_date=book_data.get("publication_date"),
            metadata={"goodreads_id": book["goodreads_id"]},
            links=[f"https://www.goodreads.com/book/show/{book['goodreads_id']}"],
            rating=int(book["rating"] * 20) if book["rating"] else None,
        )
        typer.echo(f"Adding {get_emoji_for_type(entry.type)} {entry.name} to {shelf}")
        await entry.save()


if __name__ == "__main__":
    app()
