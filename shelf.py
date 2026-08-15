from datetime import date, datetime, time as dt_time, timedelta
import json
from pathlib import Path
import re

from bson import ObjectId

from beanie import init_beanie
import typer

from shelfspace.apis.goodreads import GoodreadsAPI, build_entry, refresh_entry
from shelfspace.apis.hltb import HowlongAPI
from shelfspace.apis.steam import SteamAPI
from shelfspace.apis.tmdb import TMDBAPI
from shelfspace.estimations import round_up_game_estimate
from shelfspace.library import refresh_movies, refresh_series
from shelfspace.migration import apply_proposal, collect_proposals
from shelfspace.models import Entry, MediaType, Shelf, SubEntry
from shelfspace.models import get_emoji_for_type
from shelfspace.shelving import ShelfPlacement
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
        shelf_start = datetime.combine(shelf.start_date, dt_time(0, 0))
        shelf_end = datetime.combine(shelf.end_date + timedelta(days=1), dt_time(4, 0))

        if shelf_start <= watched_naive < shelf_end:
            return shelf

    # No matching dated shelf - use unfinished shelf with minimum weight
    unfinished_shelves = [s for s in shelves_dict.values() if not s.is_finished]
    return min(unfinished_shelves, key=lambda s: s.weight)


async def _cleanup_removed_entries(
    metadata_key: str,
    entry_types: list[str],
    current_items_by_id: dict,
    source_name: str,
):
    """Remove entries from DB that are no longer in the source.

    Only removes entries/subentries that:
    - Have the metadata_key in metadata (were added from source)
    - Are in a shelf without end_date set (Icebox, Backlog, Upcoming)
    - Are not finished

    For entries with multiple subentries, only removes unfinished subentries
    in undated shelves. If all subentries are removed, deletes the entire entry.

    Args:
        metadata_key: The metadata key to check (e.g., "goodreads_id", "hltb_id")
        entry_types: List of MediaType values to filter by
        current_items_by_id: Dictionary of current items keyed by metadata_key value
        source_name: Human-readable name of the source (e.g., "HLTB backlog")
    """
    # Load shelves to check end_date
    shelves_dict = await Shelf.get_shelves_dict()
    undated_shelf_ids = {
        shelf.id for shelf in shelves_dict.values() if shelf.end_date is None
    }

    # Find entries with subentries in undated shelves
    entries = await Entry.find(
        {
            "type": {"$in": entry_types},
            "subentries.shelf_id": {"$in": list(undated_shelf_ids)},
        }
    ).to_list()

    for entry in entries:
        item_id = entry.metadata.get(metadata_key)
        if not item_id:
            continue

        # Skip if item is still in source
        if item_id in current_items_by_id:
            continue

        # Check subentries - only remove unfinished ones in undated shelves
        subentries_to_keep = []
        subentries_removed = 0

        for sub in entry.subentries:
            # Keep if finished or in dated shelf
            if sub.is_finished or sub.shelf_id not in undated_shelf_ids:
                subentries_to_keep.append(sub)
            else:
                subentries_removed += 1

        if subentries_removed == 0:
            # No subentries to remove
            continue

        if not subentries_to_keep:
            # All subentries removed - delete entire entry
            typer.echo(f"Removing {entry.name} (no longer in {source_name})")
            await entry.delete()
        else:
            # Some subentries remain - update entry
            entry.subentries = subentries_to_keep
            typer.echo(
                f"Removed {subentries_removed} unfinished subentries from {entry.name}"
            )
            await entry.save()


@app.async_command()
async def refresh_media(movies: bool = True, series: bool = True):
    """Refresh movies and shows from TMDB.

    Everything about an unreleased title is provisional, so this is meant to run
    on a schedule. For shows it picks up newly scheduled episodes and whole new
    seasons, follows air dates that have slipped, and fills in runtimes that
    were unknown at import; for movies it does the same for release date and
    runtime. Both track the community rating.

    Unwatched episodes are then re-shelved by air date, so a delayed episode
    moves to the sprint it now belongs to. Watched subentries are never touched.
    """
    await init_db()
    api = TMDBAPI()

    if movies:
        typer.echo("Refreshing movies from TMDB...")
        for entry, changes in await refresh_movies(api):
            typer.echo(f"✓ {entry.name}: {', '.join(changes)}")

    if series:
        typer.echo("Refreshing shows from TMDB...")
        placement = ShelfPlacement(await Shelf.get_all_dict())
        for entry, changes in await refresh_series(api, placement):
            typer.echo(f"✓ {entry.name}: {', '.join(changes)}")

    typer.echo("Refresh complete")


@app.async_command()
async def migrate_to_tmdb(apply: bool = False):
    """Rebind Trakt-era entries onto TMDB ids so refresh-media can see them.

    Entries imported before the August 2026 TMDB switch still carry trakt_id
    metadata, which refresh-media does not match, so they have not been
    refreshed since. This rematches them by title and year.

    Reports what it would do and changes nothing unless --apply is passed, and
    even then only writes matches it is confident about; ambiguous ones are
    listed for you to sort out by hand. Subentries are never touched.
    """
    await init_db()
    api = TMDBAPI()

    typer.echo("Matching Trakt-era entries against TMDB...")
    proposals = await collect_proposals(api)
    if not proposals:
        typer.echo("Nothing left to migrate.")
        return

    confident = [p for p in proposals if p.confident]
    unresolved = [p for p in proposals if not p.confident]

    for proposal in sorted(confident, key=lambda p: p.label.lower()):
        seasons = (
            f" [{len(proposal.entries)} seasons]" if proposal.kind == "show" else ""
        )
        typer.echo(
            f"  ✓ {proposal.label}{seasons} -> TMDB {proposal.match['tmdb_id']} "
            f"{proposal.match['title']} ({proposal.match['year'] or '?'})"
        )
        # Episode-count gaps are worth seeing even on a match we trust.
        for note in proposal.notes[1:]:
            typer.echo(f"      note: {note}")

    for proposal in sorted(unresolved, key=lambda p: p.label.lower()):
        guess = ""
        if proposal.match:
            guess = (
                f" - closest: TMDB {proposal.match['tmdb_id']} "
                f"{proposal.match['title']} ({proposal.match['year'] or '?'})"
            )
        typer.echo(f"  ? {proposal.label}{guess}")
        for note in proposal.notes:
            typer.echo(f"      {note}")

    typer.echo(
        f"\n{len(confident)} confident, {len(unresolved)} need a decision "
        f"({sum(len(p.entries) for p in confident)} entries would be rewritten)"
    )

    if not apply:
        typer.echo("Dry run - re-run with --apply to write these.")
        return

    if not confident:
        typer.echo("Nothing confident enough to write.")
        return

    if not typer.confirm(f"Rebind {len(confident)} titles onto TMDB ids?"):
        typer.echo("Aborted.")
        raise typer.Exit(0)

    for proposal in confident:
        await apply_proposal(proposal)

    typer.echo(f"Rebound {len(confident)} titles. Run refresh-media to fill them in.")


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

        # Extract Steam ID from Steam links
        steam_id = None
        if game_data["steam_links"]:
            # Steam links look like: https://store.steampowered.com/app/123456/Game_Name/
            for link in game_data["steam_links"]:
                match = re.search(r"/app/(\d+)", link)
                if match:
                    steam_id = int(match.group(1))
                    break

        metadata = {"hltb_id": game["hltb_id"]}
        if steam_id:
            metadata["steam_id"] = steam_id

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
            metadata=metadata,
            rating=game_data["rating"],
            links=[game["url"]] + game_data["steam_links"],
        )

        typer.echo(f"Adding {entry.name} ({entry.type.value}) to Icebox")
        await entry.save()

    # Delete games that are no longer in HLTB backlog
    # Only delete if: has hltb_id, in shelf without end_date, not finished
    games_by_id = {g["hltb_id"]: g for g in games}
    await _cleanup_removed_games(games_by_id)


async def _cleanup_removed_games(hltb_games_by_id: dict[int, dict]):
    """Remove games from DB that are no longer in HLTB backlog."""
    await _cleanup_removed_entries(
        metadata_key="hltb_id",
        entry_types=[
            MediaType.GAME.value,
            MediaType.GAME_MOBILE.value,
            MediaType.GAME_VR.value,
        ],
        current_items_by_id=hltb_games_by_id,
        source_name="HLTB backlog",
    )


@app.async_command()
async def sync_steam_playtime(init_only: bool = False):
    """Sync playtime from Steam for all games with Steam IDs."""
    await init_db()

    # Load all shelves
    shelves_dict = await Shelf.get_shelves_dict()

    typer.echo("Fetching Steam playtime data...")
    api = SteamAPI()
    steam_games = await api.get_owned_games()

    # Get current shelf for current datetime
    current_shelf = get_current_shelf_for_datetime(datetime.now(), shelves_dict)

    if not current_shelf:
        typer.echo("Error: No current shelf found for current date", err=True)
        return

    typer.echo(f"Using '{current_shelf.name}' as current shelf")
    typer.echo(f"Found {len(steam_games)} games on Steam")

    added_count = 0
    updated_count = 0
    skipped_count = 0

    for game in steam_games:
        steam_id = game["appid"]
        game_name = game["name"]
        current_playtime = game.get("playtime_forever", 0)  # in minutes
        if not current_playtime:
            # No playtime recorded - skip
            skipped_count += 1
            continue

        # Find entry by steam_id
        entry = await Entry.find_one(Entry.metadata.steam_id == steam_id)

        if not entry:
            # Create new entry without subentries, store current playtime in metadata
            entry = Entry(
                type=MediaType.GAME.value,
                name=game_name,
                subentries=[],
                metadata={
                    "steam_id": steam_id,
                    "steam_playtime": current_playtime,
                },
                links=[f"https://store.steampowered.com/app/{steam_id}/"],
            )
            await entry.save()
            typer.echo(
                f"✓ Added new game: {game_name} (Steam playtime: {format_minutes(current_playtime)})"
            )
            added_count += 1
            continue

        # Entry exists - compare with last synced playtime from metadata
        last_synced_playtime = entry.metadata.get("steam_playtime", 0)

        if current_playtime <= last_synced_playtime:
            # No new playtime
            skipped_count += 1
            continue

        # Calculate difference
        playtime_diff = current_playtime - last_synced_playtime

        if not init_only:
            # Find or create subentry in current shelf
            current_subentry = None
            for sub in entry.subentries:
                if sub.shelf_id == current_shelf.id:
                    current_subentry = sub
                    break

            if not current_subentry:
                # Create new subentry in current shelf
                current_subentry = SubEntry(
                    shelf_id=current_shelf.id,
                    spent=playtime_diff,
                )
                entry.subentries.append(current_subentry)
            else:
                # Add to existing subentry
                current_subentry.spent = (current_subentry.spent or 0) + playtime_diff

        # Update metadata with current playtime
        entry.metadata["steam_playtime"] = current_playtime

        await entry.save()
        typer.echo(
            f"✓ Updated {game_name}: +{format_minutes(playtime_diff)} (Steam total: {format_minutes(current_playtime)})"
        )
        updated_count += 1

    typer.echo("\n" + "=" * 80)
    typer.echo(
        f"Added: {added_count} | Updated: {updated_count} | No updates: {skipped_count}"
    )


@app.async_command()
async def process_books():
    await init_db()
    icebox_shelf = await Shelf.find_one(Shelf.name == "Icebox")

    typer.echo("Fetching Goodreads data...")
    api = GoodreadsAPI()
    books = await api.get_to_read()
    typer.echo(f"Found {len(books)} books on the to-read shelf.")

    added_count = 0
    updated_count = 0
    missing_pages = []

    for book in books:
        if not book["page_count"]:
            missing_pages.append(book["title"])

        existing = await Entry.find_one(
            Entry.metadata["goodreads_id"] == book["goodreads_id"]
        )
        if existing:
            changes = refresh_entry(existing, book)
            if changes:
                typer.echo(
                    f"Updating {get_emoji_for_type(existing.type)} {existing.name}: "
                    + ", ".join(changes)
                )
                await existing.save()
                updated_count += 1
            continue

        entry = build_entry(book, icebox_shelf.id)
        typer.echo(f"Adding {get_emoji_for_type(entry.type)} {entry.name} to Icebox")
        await entry.save()
        added_count += 1

    typer.echo(f"\nAdded: {added_count} | Updated: {updated_count}")

    if missing_pages:
        typer.echo(
            f"\nStill no page count in the feed for {len(missing_pages)} book(s), "
            "so they carry no estimate yet:"
        )
        for title in missing_pages:
            typer.echo(f"  - {title}")

    # Delete books that are no longer on Goodreads to-read list
    # Only delete if: has goodreads_id, in shelf without end_date, not finished
    books_by_id = {b["goodreads_id"]: b for b in books}
    await _cleanup_removed_books(books_by_id)


async def _cleanup_removed_books(goodreads_books_by_id: dict[int, dict]):
    """Remove books from DB that are no longer on Goodreads to-read list."""
    await _cleanup_removed_entries(
        metadata_key="goodreads_id",
        entry_types=[
            MediaType.BOOK.value,
            MediaType.BOOK_COM.value,
            MediaType.BOOK_ED.value,
        ],
        current_items_by_id=goodreads_books_by_id,
        source_name="Goodreads to-read list",
    )


@app.async_command()
async def export_data(output: str = "shelfspace-export.json"):
    """Export all data to JSON file.

    Args:
        output: Output file path (default: shelfspace-export.json)
    """
    await init_db()

    # Fetch all data
    shelves = await Shelf.find().to_list()
    entries = await Entry.find().to_list()

    # Build export structure
    export = {
        "exported_at": datetime.now().isoformat(),
        "shelves": [shelf.model_dump() for shelf in shelves],
        "entries": [entry.model_dump() for entry in entries],
    }

    # Write to file
    output_path = Path(output)
    output_path.write_text(json.dumps(export, indent=2, default=str))

    typer.echo(
        f"Exported {len(shelves)} shelves and {len(entries)} entries to {output}"
    )


@app.async_command()
async def import_data(
    input_file: str = "shelfspace-export.json",
    clear: bool = False,
):
    """Import data from JSON file.

    Args:
        input_file: Input file path (default: shelfspace-export.json)
        clear: Clear all existing data before importing
    """
    input_path = Path(input_file)
    if not input_path.exists():
        typer.echo(f"Error: File not found: {input_file}")
        raise typer.Exit(1)

    await init_db()

    # Read and parse JSON
    data = json.loads(input_path.read_text())

    if clear:
        confirm = typer.confirm(
            "This will DELETE all existing shelves and entries. Continue?"
        )
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(0)

        deleted_shelves = await Shelf.find().delete()
        deleted_entries = await Entry.find().delete()
        typer.echo(
            f"Cleared {deleted_shelves.deleted_count} shelves and "
            f"{deleted_entries.deleted_count} entries"
        )

    # Import shelves first (entries reference them)
    shelves_added = 0
    shelves_skipped = 0
    for shelf_data in data.get("shelves", []):
        shelf_id = ObjectId(shelf_data["id"])

        # Check if shelf already exists
        existing = await Shelf.find_one({"_id": shelf_id})
        if existing:
            shelves_skipped += 1
            continue

        # Parse dates
        start_date = None
        if shelf_data.get("start_date"):
            start_date = date.fromisoformat(shelf_data["start_date"])

        end_date = None
        if shelf_data.get("end_date"):
            end_date = date.fromisoformat(shelf_data["end_date"])

        created_at = datetime.fromisoformat(shelf_data["created_at"])
        updated_at = datetime.fromisoformat(shelf_data["updated_at"])

        shelf = Shelf(
            id=shelf_id,
            name=shelf_data["name"],
            start_date=start_date,
            end_date=end_date,
            description=shelf_data.get("description", ""),
            is_finished=shelf_data.get("is_finished", False),
            weight=shelf_data.get("weight", 1),
            created_at=created_at,
            updated_at=updated_at,
        )
        await shelf.insert()
        shelves_added += 1

    typer.echo(f"Shelves: {shelves_added} added, {shelves_skipped} skipped (existing)")

    # Import entries
    entries_added = 0
    entries_skipped = 0
    for entry_data in data.get("entries", []):
        entry_id = ObjectId(entry_data["id"])

        # Check if entry already exists
        existing = await Entry.find_one({"_id": entry_id})
        if existing:
            entries_skipped += 1
            continue

        # Parse release date
        release_date = None
        if entry_data.get("release_date"):
            release_date = date.fromisoformat(entry_data["release_date"])

        created_at = datetime.fromisoformat(entry_data["created_at"])
        updated_at = datetime.fromisoformat(entry_data["updated_at"])

        # Parse subentries
        subentries = []
        for sub_data in entry_data.get("subentries", []):
            sub_release_date = None
            if sub_data.get("release_date"):
                sub_release_date = date.fromisoformat(sub_data["release_date"])

            shelf_id = None
            if sub_data.get("shelf_id"):
                shelf_id = ObjectId(sub_data["shelf_id"])

            subentries.append(
                SubEntry(
                    shelf=sub_data.get("shelf", ""),
                    shelf_id=shelf_id,
                    name=sub_data.get("name", ""),
                    estimated=sub_data.get("estimated"),
                    spent=sub_data.get("spent", 0),
                    is_finished=sub_data.get("is_finished", False),
                    release_date=sub_release_date,
                    metadata=sub_data.get("metadata", {}),
                )
            )

        entry = Entry(
            id=entry_id,
            type=MediaType(entry_data["type"]),
            name=entry_data["name"],
            notes=entry_data.get("notes", ""),
            release_date=release_date,
            rating=entry_data.get("rating"),
            metadata=entry_data.get("metadata", {}),
            links=entry_data.get("links", []),
            subentries=subentries,
            created_at=created_at,
            updated_at=updated_at,
        )
        await entry.insert()
        entries_added += 1

    typer.echo(f"Entries: {entries_added} added, {entries_skipped} skipped (existing)")
    typer.echo(f"Import complete from {input_file}")


if __name__ == "__main__":
    app()
