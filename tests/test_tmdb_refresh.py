"""Covers the rules that decide what a refresh is allowed to overwrite."""

from datetime import date

from shelfspace.apis.tmdb import (
    build_movie_entry,
    build_season_entry,
    refresh_movie_entry,
    refresh_season_entry,
)
from shelfspace.models import MediaType
from shelfspace.shelving import ShelfPlacement


def show_payload(**overrides) -> dict:
    return {
        "tmdb_id": 42,
        "name": "Test Show",
        "release_date": date(2026, 8, 5),
        "rating": 80,
        "status": "Returning Series",
        "in_production": True,
        "seasons": [{"number": 1, "episode_count": 2, "air_date": date(2026, 8, 5)}],
        "is_multi_season": False,
    } | overrides


def episode(number: int, runtime: int | None, air_date: date | None) -> dict:
    return {
        "number": number,
        "season_number": 1,
        "name": f"Episode {number}",
        "runtime": runtime,
        "air_date": air_date,
    }


def test_season_import_places_episodes_by_air_date(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}

    entry = build_season_entry(
        show_payload(),
        1,
        [
            episode(1, 50, date(2026, 8, 5)),  # inside week1
            episode(2, 50, date(2026, 8, 12)),  # inside week2
            episode(3, None, None),  # unscheduled
        ],
        lambda air_date: placement.resolve(air_date),
    )

    assert [s.name for s in entry.subentries] == ["S01E01", "S01E02", "S01E03"]
    assert entry.subentries[0].shelf_id == by_name["week1"]
    assert entry.subentries[1].shelf_id == by_name["week2"]
    # Nothing is known about when it airs, so it waits in the Backlog.
    assert entry.subentries[2].shelf_id == by_name["Backlog"]


def test_parked_show_sends_every_episode_to_icebox(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}

    entry = build_season_entry(
        show_payload(),
        1,
        [episode(1, 50, date(2026, 8, 5)), episode(2, 50, date(2026, 8, 12))],
        lambda air_date: placement.resolve(air_date, parked=True),
    )

    assert {s.shelf_id for s in entry.subentries} == {by_name["Icebox"]}
    assert placement.is_parked([entry])


def test_refresh_adds_newly_scheduled_episodes(shelves):
    placement = ShelfPlacement(shelves)
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )

    changes = refresh_season_entry(
        entry,
        show,
        [episode(1, 50, date(2026, 8, 5)), episode(2, 48, date(2026, 8, 12))],
        placement.resolve,
    )

    assert "added S01E02" in changes
    assert len(entry.subentries) == 2
    assert entry.subentries[1].estimated == 48


def test_refresh_fills_unknown_runtime_and_follows_slipped_air_date(shelves):
    placement = ShelfPlacement(shelves)
    show = show_payload()
    entry = build_season_entry(show, 1, [episode(1, None, None)], placement.resolve)
    assert entry.subentries[0].estimated is None

    changes = refresh_season_entry(
        entry, show, [episode(1, 45, date(2026, 8, 12))], placement.resolve
    )

    assert entry.subentries[0].estimated == 45
    assert entry.subentries[0].release_date == date(2026, 8, 12)
    assert any("runtime" in c for c in changes)
    assert any("air date" in c for c in changes)


def test_refresh_leaves_watched_episodes_alone(shelves):
    placement = ShelfPlacement(shelves)
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )

    watched = entry.subentries[0]
    watched.mark_watched()
    original_shelf = watched.shelf_id

    # TMDB now claims a different runtime and a different air date.
    refresh_season_entry(
        entry, show, [episode(1, 90, date(2026, 8, 12))], placement.resolve
    )

    assert watched.estimated == 50
    assert watched.spent == 50
    assert watched.release_date == date(2026, 8, 5)

    # Re-shelving must not drag watched episodes off the shelf they were seen on.
    placement.reassign([entry], parked=False)
    assert watched.shelf_id == original_shelf


def test_reassign_moves_unwatched_episode_to_the_shelf_its_air_date_landed_in(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )
    assert entry.subentries[0].shelf_id == by_name["week1"]

    refresh_season_entry(
        entry, show, [episode(1, 50, date(2026, 8, 12))], placement.resolve
    )
    moved = placement.reassign([entry], parked=False)

    assert moved == 1
    assert entry.subentries[0].shelf_id == by_name["week2"]


def test_renewed_show_gets_renamed_on_import(shelves):
    placement = ShelfPlacement(shelves)
    single = build_season_entry(
        show_payload(is_multi_season=False),
        1,
        [episode(1, 50, None)],
        placement.resolve,
    )
    renewed = build_season_entry(
        show_payload(is_multi_season=True), 2, [episode(1, 50, None)], placement.resolve
    )

    assert single.name == "Test Show"
    assert renewed.name == "Test Show S2"


def movie_payload(**overrides) -> dict:
    return {
        "tmdb_id": 7,
        "title": "Test Movie",
        "release_date": date(2026, 12, 1),
        "runtime": 120,
        "rating": 70,
        "status": "Released",
    } | overrides


def test_movie_refresh_fills_runtime_and_release_date(shelves):
    shelf_id = next(iter(shelves))
    entry = build_movie_entry(
        movie_payload(runtime=None, release_date=None, rating=None), shelf_id
    )
    assert entry.type == MediaType.MOVIE
    assert entry.subentries[0].estimated is None

    changes = refresh_movie_entry(entry, movie_payload())

    assert entry.subentries[0].estimated == 120
    assert entry.release_date == date(2026, 12, 1)
    assert entry.rating == 70
    assert len(changes) == 3


def test_movie_refresh_leaves_a_watched_movie_alone(shelves):
    shelf_id = next(iter(shelves))
    entry = build_movie_entry(movie_payload(), shelf_id)
    entry.subentries[0].mark_watched()

    refresh_movie_entry(entry, movie_payload(runtime=200))

    assert entry.subentries[0].estimated == 120
    assert entry.subentries[0].spent == 120
