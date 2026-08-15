"""Covers the rules that decide when a Trakt entry may be rebound to TMDB.

The stakes here are one-sided: leaving a title unmatched costs a minute of
manual work, while binding one to the wrong show is silent and permanent, so
most of these tests are about what the matcher refuses to do.
"""

from datetime import date

import pytest

from shelfspace.migration import (
    Proposal,
    _pick_movie,
    _score_show,
    _slug_year,
    apply_proposal,
    rebind,
)
from shelfspace.models import Entry, MediaType, SubEntry


def candidate(tmdb_id: int, title: str, year: int | None, popularity: float) -> dict:
    return {
        "tmdb_id": tmdb_id,
        "title": title,
        "year": year,
        "popularity": popularity,
    }


def season_entry(number: int, episodes: int, release: date | None) -> Entry:
    return Entry(
        type=MediaType.SERIES,
        name=f"Test Show S{number}",
        release_date=release,
        metadata={"trakt_id": f"99_s{number}", "show_trakt_id": 99},
        links=[f"https://trakt.tv/shows/test-show/seasons/{number}"],
        subentries=[
            SubEntry(name=f"S{number:02d}E{n:02d}") for n in range(1, episodes + 1)
        ],
    )


def show_payload(seasons: list[dict]) -> dict:
    return {"tmdb_id": 7, "name": "Test Show", "seasons": seasons}


def season(number: int, episode_count: int, air_date: date | None) -> dict:
    return {"number": number, "episode_count": episode_count, "air_date": air_date}


def test_slug_year_reads_the_year_trakt_appends():
    assert _slug_year("alice-in-borderland-2020", "Alice in Borderland") == 2020


def test_slug_year_ignores_a_year_that_is_part_of_the_title():
    # "blade-runner-2049" ends in a plausible year that is really the name.
    assert _slug_year("blade-runner-2049", "Blade Runner 2049") is None


def test_slug_year_ignores_a_slug_with_no_year():
    assert _slug_year("inside-no-9", "Inside No. 9") is None


def test_movie_matches_on_title_and_year():
    match, confident, _ = _pick_movie(
        [
            candidate(564, "The Mummy", 1999, 26.5),
            candidate(282035, "The Mummy", 2017, 14.5),
        ],
        "The Mummy",
        1999,
    )
    assert confident
    assert match["tmdb_id"] == 564


def test_movie_ignores_the_noise_records_sharing_a_title_and_year():
    # TMDB carries shorts and duplicates under the real film's title; they sit
    # orders of magnitude below it in popularity.
    match, confident, _ = _pick_movie(
        [candidate(823219, "Flow", 2024, 23.8), candidate(1390255, "Flow", 2024, 0.5)],
        "Flow",
        2024,
    )
    assert confident
    assert match["tmdb_id"] == 823219


def test_movie_refuses_two_real_films_sharing_a_title_and_year():
    match, confident, reason = _pick_movie(
        [candidate(613911, "Bliss", 2021, 3.3), candidate(795410, "Bliss", 2021, 2.5)],
        "Bliss",
        2021,
    )
    assert not confident
    assert "comparable" in reason
    # Still reported, so a human has somewhere to start.
    assert match is not None


def test_movie_prefers_an_exact_year_over_a_neighbouring_one():
    match, confident, _ = _pick_movie(
        [
            candidate(111, "Novocaine", 2024, 1.6),
            candidate(222, "Novocaine", 2025, 13.4),
        ],
        "Novocaine",
        2025,
    )
    assert confident
    assert match["tmdb_id"] == 222


def test_movie_tolerates_a_year_of_drift():
    # Festival year on one source, wide release on the other.
    match, confident, _ = _pick_movie(
        [candidate(63, "The Hurt Locker", 2008, 20.0)], "The Hurt Locker", 2009
    )
    assert confident
    assert match["tmdb_id"] == 63


def test_movie_refuses_a_title_match_that_is_years_away():
    match, confident, reason = _pick_movie(
        [candidate(1, "Solaris", 1972, 12.0)], "Solaris", 2002
    )
    assert not confident
    assert "1972" in reason


def test_movie_refuses_when_nothing_matches_the_title():
    match, confident, reason = _pick_movie(
        [candidate(1, "Something Else", 2020, 9.0)], "The Punisher", 2020
    )
    assert not confident
    assert reason == "no exact title match"


def test_movie_reports_an_empty_search():
    match, confident, reason = _pick_movie([], "Nothing At All", 2020)
    assert match is None
    assert not confident
    assert reason == "no TMDB result"


def test_show_score_rewards_agreement_on_air_dates():
    entries = [
        season_entry(1, 6, date(2013, 1, 15)),
        season_entry(2, 6, date(2014, 7, 14)),
    ]
    right = show_payload(
        [season(1, 6, date(2013, 1, 15)), season(2, 6, date(2014, 7, 14))]
    )
    # Same title, same era, unrelated show: a daily soap with hundreds of parts.
    wrong = show_payload(
        [season(1, 258, date(2014, 9, 29)), season(2, 27, date(2015, 1, 5))]
    )

    assert _score_show(right, entries) == (2, 2, 2)
    assert _score_show(wrong, entries) == (0, 0, 2)


def test_show_score_tolerates_episodes_not_yet_tracked():
    # Half a season watched here, the whole thing listed on TMDB.
    entries = [season_entry(3, 9, date(2026, 7, 3))]
    show = show_payload([season(3, 10, date(2026, 7, 3))])
    assert _score_show(show, entries) == (1, 1, 1)


def test_show_score_ignores_a_season_the_candidate_does_not_have():
    entries = [season_entry(4, 12, date(2026, 6, 3))]
    show = show_payload([season(1, 12, date(2022, 1, 27))])
    assert _score_show(show, entries) == (0, 0, 0)


def test_rebind_writes_what_a_fresh_import_would_have_written():
    entry = Entry(
        type=MediaType.MOVIE,
        name="Forrest Gump",
        metadata={"trakt_id": 9},
        links=[
            "https://trakt.tv/movies/forrest-gump-1994",
            "https://example.com/notes",
        ],
    )

    rebind(entry, "movie", 13)

    assert entry.metadata["tmdb_id"] == 13
    assert entry.metadata["tmdb_type"] == "movie"
    # Kept, so it stays visible what the entry was rebound from.
    assert entry.metadata["trakt_id"] == 9
    assert entry.links == [
        "https://www.themoviedb.org/movie/13",
        "https://example.com/notes",
    ]


def test_rebind_gives_a_season_the_key_refresh_looks_it_up_by():
    entry = season_entry(2, 8, date(2026, 1, 1))

    rebind(entry, "show", 1396)

    assert entry.metadata["tmdb_id"] == "1396_s2"
    assert entry.metadata["tmdb_show_id"] == 1396
    assert entry.metadata["tmdb_season"] == 2
    assert entry.metadata["tmdb_type"] == "season"
    assert entry.links == ["https://www.themoviedb.org/tv/1396/season/2"]


def test_rebind_leaves_subentries_untouched():
    entry = season_entry(1, 3, date(2026, 1, 1))
    entry.subentries[0].estimated = 52
    entry.subentries[0].spent = 52
    entry.subentries[0].is_finished = True
    before = [s.model_dump() for s in entry.subentries]

    rebind(entry, "show", 1396)

    assert [s.model_dump() for s in entry.subentries] == before


async def test_applying_a_proposal_without_a_match_is_refused():
    proposal = Proposal(kind="movie", entries=[], title="Unmatched", year=None)
    with pytest.raises(ValueError):
        await apply_proposal(proposal)
