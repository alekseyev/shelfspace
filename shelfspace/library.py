"""Imports and refreshes movie/series entries from TMDB.

Sits between the TMDB client and its two callers -- the GUI's add dialog and the
``refresh-media`` command -- so both agree on what importing a title means and
neither reimplements deduplication.

Refreshing exists because metadata for anything unreleased is provisional: TMDB
schedules episodes weeks after a season is announced, air dates slip, runtimes
land close to broadcast, and ratings drift for months. Watched subentries are
never touched, since those record what actually happened.
"""

from collections import defaultdict

from bson import ObjectId
from loguru import logger

from shelfspace.apis.tmdb import (
    TMDBAPI,
    build_movie_entry,
    build_season_entry,
    entry_name_for_season,
    refresh_movie_entry,
    refresh_season_entry,
    season_key,
)
from shelfspace.models import Entry
from shelfspace.shelving import ShelfPlacement

# Only a show TMDB still considers in production can gain episodes.
GROWING_STATUSES = {"Returning Series", "In Production", "Planned"}


async def import_movie(api: TMDBAPI, tmdb_id: int, shelf_id: ObjectId) -> Entry | None:
    """Import a movie. Returns None when it is already in the database."""
    if await Entry.find_one(Entry.metadata["tmdb_id"] == tmdb_id):
        return None

    movie = await api.get_movie(tmdb_id)
    entry = build_movie_entry(movie, shelf_id)
    await entry.save()
    return entry


async def import_series(
    api: TMDBAPI, tmdb_id: int, placement: ShelfPlacement, parked: bool
) -> list[Entry]:
    """Import every season of a show as its own Entry, skipping known seasons.

    Episodes are placed by air date so a part-aired season lands across the
    right shelves, unless the show is parked, which sends everything to Icebox.
    """
    show = await api.get_show(tmdb_id)
    seasons = await api.get_all_seasons(show)

    created = []
    for season_number, episodes in sorted(seasons.items()):
        if not episodes:
            continue
        if await Entry.find_one(
            Entry.metadata["tmdb_id"] == season_key(tmdb_id, season_number)
        ):
            continue

        entry = build_season_entry(
            show,
            season_number,
            episodes,
            lambda air_date: placement.resolve(air_date, parked),
        )
        await entry.save()
        created.append(entry)

    return created


def should_add_season(
    season: dict, earliest_tracked: int | None, can_grow: bool
) -> bool:
    """Whether a season TMDB lists but nothing here tracks should be created.

    Seasons below the earliest one tracked are back catalogue left out on
    purpose -- picking a show up at its current season is normal, and a refresh
    has no business dragging its history in behind it. Above that line, an
    unscheduled season only counts once the show is one that can still grow.
    """
    if earliest_tracked is not None and season["number"] < earliest_tracked:
        return False
    return can_grow or bool(season["air_date"])


async def refresh_movies(api: TMDBAPI) -> list[tuple[Entry, list[str]]]:
    """Update unwatched movies from TMDB. Returns what changed, per entry."""
    entries = await Entry.find(Entry.metadata["tmdb_type"] == "movie").to_list()

    results = []
    for entry in entries:
        # A fully watched movie has nothing left to predict.
        if all(subentry.is_finished for subentry in entry.subentries):
            continue

        movie = await api.get_movie(entry.metadata["tmdb_id"])
        changes = refresh_movie_entry(entry, movie)
        if changes:
            await entry.save()
            results.append((entry, changes))

    return results


async def refresh_series(
    api: TMDBAPI, placement: ShelfPlacement
) -> list[tuple[Entry, list[str]]]:
    """Update tracked shows: new seasons, new episodes, schedule and rating drift.

    Shelf placement is recomputed for every unwatched episode afterwards, so an
    episode that slipped into next month follows its air date onto the right
    shelf without anyone moving it by hand.
    """
    entries = await Entry.find(Entry.metadata["tmdb_type"] == "season").to_list()

    by_show: dict[int, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_show[entry.metadata["tmdb_show_id"]].append(entry)

    results = []
    for show_id, show_entries in by_show.items():
        has_unwatched = any(
            not subentry.is_finished
            for entry in show_entries
            for subentry in entry.subentries
        )

        show = await api.get_show(show_id)
        can_grow = show["status"] in GROWING_STATUSES or show["in_production"]
        # Finished watching an ended show: nothing to check ever again.
        if not has_unwatched and not can_grow:
            continue

        parked = placement.is_parked(show_entries)

        def shelf_id_for(air_date, parked=parked):
            return placement.resolve(air_date, parked)

        tracked = {entry.metadata["tmdb_season"]: entry for entry in show_entries}
        earliest_tracked = min(tracked, default=None)

        for season in show["seasons"]:
            number = season["number"]
            entry = tracked.get(number)

            if entry is None:
                if not should_add_season(season, earliest_tracked, can_grow):
                    continue
                episodes = await api.get_season_episodes(show_id, number)
                if not episodes:
                    continue
                new_entry = build_season_entry(show, number, episodes, shelf_id_for)
                await new_entry.save()
                show_entries.append(new_entry)
                results.append((new_entry, [f"new season, {len(episodes)} episodes"]))
                continue

            # A fully watched season of a show that can still grow may yet gain
            # episodes, so it is only skipped once the show has ended.
            if all(s.is_finished for s in entry.subentries) and not can_grow:
                continue

            episodes = await api.get_season_episodes(show_id, number)
            changes = refresh_season_entry(entry, show, episodes, shelf_id_for)

            # A renewed show turns "Show" into "Show S1".
            new_name = entry_name_for_season(
                show["name"], number, show["is_multi_season"]
            )
            if new_name != entry.name:
                changes.append(f"renamed to {new_name}")
                entry.name = new_name

            if changes:
                await entry.save()
                results.append((entry, changes))

        moved = placement.reassign(show_entries, parked)
        if moved:
            for entry in show_entries:
                await entry.save()
            logger.info(f"{show['name']}: moved {moved} episodes between shelves")

    return results
