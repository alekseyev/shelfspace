"""Covers the rules that decide what a refresh is allowed to overwrite."""

from datetime import date

from shelfspace.apis.tmdb import (
    build_movie_entry,
    build_season_entry,
    refresh_movie_entry,
    refresh_season_entry,
)
from shelfspace.library import parked_at, should_add_season
from shelfspace.models import Entry, MediaType
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
    placement.reassign([entry])
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
    moved = placement.reassign([entry])

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


def test_refresh_skips_seasons_older_than_anything_tracked():
    # Picked the show up at S9; S1-S8 are history that was left out on purpose.
    assert not should_add_season(
        {"number": 3, "air_date": date(2019, 11, 10)}, earliest_tracked=9, can_grow=True
    )


def test_refresh_adds_a_season_after_the_ones_tracked():
    assert should_add_season(
        {"number": 10, "air_date": date(2026, 9, 1)}, earliest_tracked=9, can_grow=True
    )


def test_refresh_adds_every_season_when_none_is_tracked_yet():
    assert should_add_season(
        {"number": 1, "air_date": date(2019, 11, 10)},
        earliest_tracked=None,
        can_grow=False,
    )


def test_refresh_ignores_an_unscheduled_season_of_a_finished_show():
    assert not should_add_season(
        {"number": 10, "air_date": None}, earliest_tracked=9, can_grow=False
    )


def test_reassign_leaves_an_unwatched_episode_that_aired_weeks_ago(shelves):
    """The Lucky case: a season kept together on the current sprint stays put."""
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 7, 28))], placement.resolve
    )
    # Aired during a sprint that has since closed, so import parks it in Backlog.
    assert entry.subentries[0].shelf_id == by_name["Backlog"]

    # The episode is then pulled onto the current sprint by hand, unwatched.
    entry.subentries[0].shelf_id = by_name["week1"]

    assert placement.reassign([entry]) == 0
    assert entry.subentries[0].shelf_id == by_name["week1"]


def test_reassign_never_pulls_an_episode_back_to_its_air_date_shelf(shelves):
    """Deliberately held back to be binged once the season has finished."""
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )
    assert entry.subentries[0].shelf_id == by_name["week1"]

    entry.subentries[0].shelf_id = by_name["week2"]

    assert placement.reassign([entry]) == 0
    assert entry.subentries[0].shelf_id == by_name["week2"]


def test_reassign_still_follows_an_air_date_that_slipped_later(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )
    refresh_season_entry(
        entry, show, [episode(1, 50, date(2026, 8, 12))], placement.resolve
    )

    assert placement.reassign([entry]) == 1
    assert entry.subentries[0].shelf_id == by_name["week2"]


def test_resolve_never_schedules_onto_a_finished_shelf(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    # Squarely inside the closed sprint's range, which is closed to new work.
    assert placement.resolve(date(2026, 7, 29)) == by_name["Backlog"]


def test_reassign_never_takes_an_episode_out_of_the_icebox(shelves):
    """The Icebox is a hold placed by hand; an air date must not undo it."""
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    entry = build_season_entry(
        show, 1, [episode(1, 50, date(2026, 8, 12))], placement.resolve
    )
    assert entry.subentries[0].shelf_id == by_name["week2"]

    entry.subentries[0].shelf_id = by_name["Icebox"]

    assert placement.reassign([entry]) == 0
    assert entry.subentries[0].shelf_id == by_name["Icebox"]


def test_reassign_never_sweeps_a_show_into_the_icebox(shelves):
    """The Gentlemen case: S2 iced to wait its turn, S1 stays in the Backlog."""
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    season1 = build_season_entry(show, 1, [episode(1, 50, None)], placement.resolve)
    season2 = build_season_entry(
        show, 2, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )
    season2.subentries[0].shelf_id = by_name["Icebox"]

    assert placement.reassign([season1, season2]) == 0
    assert season1.subentries[0].shelf_id == by_name["Backlog"]
    assert season2.subentries[0].shelf_id == by_name["Icebox"]


def test_parking_carries_forward_to_later_seasons_but_not_back(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}

    def season(number: int, shelf: str) -> Entry:
        entry = build_season_entry(
            show_payload(), number, [episode(1, 50, None)], placement.resolve
        )
        entry.metadata["tmdb_season"] = number
        entry.subentries[0].shelf_id = by_name[shelf]
        return entry

    entries = [season(1, "Backlog"), season(2, "Icebox")]

    # A new episode of S1 is scheduled normally; one of S3 waits behind S2.
    assert not parked_at(placement, entries, 1)
    assert parked_at(placement, entries, 2)
    assert parked_at(placement, entries, 3)


def test_reassign_schedules_an_episode_waiting_in_the_backlog(shelves):
    placement = ShelfPlacement(shelves)
    by_name = {shelf.name: shelf.id for shelf in shelves.values()}
    show = show_payload()
    entry = build_season_entry(show, 1, [episode(1, 50, None)], placement.resolve)
    assert entry.subentries[0].shelf_id == by_name["Backlog"]

    refresh_season_entry(
        entry, show, [episode(1, 50, date(2026, 8, 5))], placement.resolve
    )

    assert placement.reassign([entry]) == 1
    assert entry.subentries[0].shelf_id == by_name["week1"]
