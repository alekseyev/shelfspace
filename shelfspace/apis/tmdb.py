"""Reads movie and TV metadata from TMDB.

Replaces the Trakt integration, which lost API access in August 2026. TMDB covers
films and shows under one id space and reports a runtime per *episode*, which is
the unit every Shelfspace estimate is built from — Simkl, the other obvious
Trakt replacement, only reports runtime at show level.

Authentication is a read access token (bearer), free for personal use from
https://www.themoviedb.org/settings/api. Set it as ``SET_TMDB_TOKEN``.

Like ``goodreads.py``, this module owns both the HTTP client and the Entry
construction, so the GUI and CLI share one definition of what an imported movie
or season looks like.
"""

import asyncio
from collections.abc import Callable
from datetime import date, datetime

import aiohttp
from bson import ObjectId

from shelfspace.models import Entry, MediaType, SubEntry
from shelfspace.settings import settings
from shelfspace.utils import format_minutes

# Season 0 is specials on TMDB; Shelfspace has never tracked them.
SPECIALS_SEASON = 0

# Resolves an episode's air date to the shelf it belongs on. Supplied by the
# caller because shelf placement is Shelfspace policy, not TMDB data.
type ShelfResolver = Callable[[date | None], ObjectId]


def _parse_date(text: str | None) -> date | None:
    """TMDB dates are ISO, but unreleased items carry an empty string."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _rating(vote_average: float | None) -> int | None:
    """Convert TMDB's 0-10 average to the 0-100 scale Entry uses."""
    if not vote_average:
        return None
    return int(vote_average * 10)


def episode_name(season_number: int, episode_number: int) -> str:
    """The SubEntry name format the whole codebase matches episodes on."""
    return f"S{season_number:02d}E{episode_number:02d}"


def season_key(show_id: int, season_number: int) -> str:
    """Dedup key for a season Entry, mirroring the old Trakt season keys."""
    return f"{show_id}_s{season_number}"


def entry_name_for_season(
    show_name: str, season_number: int, is_multi_season: bool
) -> str:
    """ "Show" for a single-season show, "Show S2" once there is more than one."""
    if is_multi_season:
        return f"{show_name} S{season_number}"
    return show_name


def build_movie_entry(movie: dict, shelf_id: ObjectId) -> Entry:
    """Create an Entry for a movie being imported for the first time."""
    return Entry(
        type=MediaType.MOVIE,
        name=movie["title"],
        subentries=[
            SubEntry(
                shelf_id=shelf_id,
                estimated=movie["runtime"],
                release_date=movie["release_date"],
            )
        ],
        release_date=movie["release_date"],
        rating=movie["rating"],
        metadata={"tmdb_id": movie["tmdb_id"], "tmdb_type": "movie"},
        links=[f"https://www.themoviedb.org/movie/{movie['tmdb_id']}"],
    )


def build_season_entry(
    show: dict,
    season_number: int,
    episodes: list[dict],
    shelf_id_for: ShelfResolver,
) -> Entry:
    """Create an Entry for one season, with a SubEntry per episode.

    ``shelf_id_for`` picks the shelf per episode from its air date, so a season
    that straddles a sprint boundary lands on the right shelves immediately.
    """
    subentries = [
        SubEntry(
            shelf_id=shelf_id_for(episode["air_date"]),
            name=episode_name(season_number, episode["number"]),
            estimated=episode["runtime"],
            release_date=episode["air_date"],
        )
        for episode in episodes
    ]

    return Entry(
        type=MediaType.SERIES,
        name=entry_name_for_season(
            show["name"], season_number, show["is_multi_season"]
        ),
        subentries=subentries,
        release_date=subentries[0].release_date if subentries else None,
        rating=show["rating"],
        metadata={
            "tmdb_id": season_key(show["tmdb_id"], season_number),
            "tmdb_show_id": show["tmdb_id"],
            "tmdb_season": season_number,
            "tmdb_type": "season",
        },
        links=[
            f"https://www.themoviedb.org/tv/{show['tmdb_id']}/season/{season_number}"
        ],
    )


def refresh_movie_entry(entry: Entry, movie: dict) -> list[str]:
    """Apply fresh TMDB data to an already imported movie.

    Unreleased films are the case that matters: TMDB fills in the runtime and
    firms up the release date long after the film first appears, and the
    community rating keeps drifting afterwards. Returns a description of every
    field changed, empty when nothing moved.
    """
    changes = []

    new_date = movie["release_date"]
    if new_date and entry.release_date != new_date:
        old_date = entry.release_date
        entry.release_date = new_date
        for subentry in entry.subentries:
            if not subentry.is_finished and subentry.release_date == old_date:
                subentry.release_date = new_date
        changes.append(f"release {old_date or '?'} -> {new_date}")

    if movie["rating"] is not None and movie["rating"] != entry.rating:
        changes.append(f"rating {entry.rating or '?'} -> {movie['rating']}")
        entry.rating = movie["rating"]

    # A watched movie's spent time is history; only an unwatched estimate is
    # still a prediction that a corrected runtime should update.
    runtime = movie["runtime"]
    unfinished = [s for s in entry.subentries if not s.is_finished]
    if runtime and len(unfinished) == 1 and unfinished[0].estimated != runtime:
        old_runtime = unfinished[0].estimated
        unfinished[0].estimated = runtime
        changes.append(
            f"runtime {format_minutes(old_runtime) if old_runtime else '?'} -> "
            f"{format_minutes(runtime)}"
        )

    return changes


def refresh_season_entry(
    entry: Entry,
    show: dict,
    episodes: list[dict],
    shelf_id_for: ShelfResolver,
) -> list[str]:
    """Apply fresh TMDB data to an already imported season.

    This is the ongoing-show case: episodes get scheduled after the season is
    first imported, air dates slip, and runtimes are unknown until close to
    broadcast. Finished episodes are left alone entirely — they are a record of
    what was watched, not a prediction.
    """
    changes = []

    if show["rating"] is not None and show["rating"] != entry.rating:
        changes.append(f"rating {entry.rating or '?'} -> {show['rating']}")
        entry.rating = show["rating"]

    subentries_by_name = {s.name: s for s in entry.subentries}

    for episode in episodes:
        name = episode_name(episode["season_number"], episode["number"])
        air_date = episode["air_date"]
        runtime = episode["runtime"]
        subentry = subentries_by_name.get(name)

        if subentry is None:
            entry.subentries.append(
                SubEntry(
                    shelf_id=shelf_id_for(air_date),
                    name=name,
                    estimated=runtime,
                    release_date=air_date,
                )
            )
            changes.append(f"added {name}")
            continue

        if subentry.is_finished:
            continue

        if runtime and subentry.estimated != runtime:
            old_runtime = subentry.estimated
            subentry.estimated = runtime
            changes.append(
                f"{name} runtime "
                f"{format_minutes(old_runtime) if old_runtime else '?'} -> "
                f"{format_minutes(runtime)}"
            )

        if air_date and subentry.release_date != air_date:
            changes.append(
                f"{name} air date {subentry.release_date or '?'} -> {air_date}"
            )
            subentry.release_date = air_date

    if entry.subentries and entry.subentries[0].release_date != entry.release_date:
        entry.release_date = entry.subentries[0].release_date

    return changes


class TMDBAPI:
    base_url = "https://api.themoviedb.org/3"

    def __init__(self, token: str | None = None):
        self.token = token if token is not None else settings.TMDB_TOKEN

    def _session(self) -> aiohttp.ClientSession:
        if not self.token:
            raise ValueError(
                "SET_TMDB_TOKEN is not configured - create a free read access "
                "token at https://www.themoviedb.org/settings/api"
            )
        return aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.token}",
                "accept": "application/json",
            }
        )

    async def _get(
        self, session: aiohttp.ClientSession, path: str, params: dict | None = None
    ) -> dict:
        async with session.get(f"{self.base_url}{path}", params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search movies and shows at once, best match first.

        Feeds the GUI's add-entry autocomplete, so results carry only what the
        picker shows: what it is, what it is called, and when it came out.
        """
        query = query.strip()
        if not query:
            return []

        async with self._session() as session:
            payload = await self._get(
                session, "/search/multi", {"query": query, "include_adult": "false"}
            )

        results = []
        for item in payload.get("results", []):
            media_type = item.get("media_type")
            if media_type not in ("movie", "tv"):
                continue

            release = _parse_date(
                item.get("release_date") or item.get("first_air_date")
            )
            results.append(
                {
                    "tmdb_id": item["id"],
                    "media_type": MediaType.MOVIE
                    if media_type == "movie"
                    else MediaType.SERIES,
                    "title": item.get("title") or item.get("name") or "",
                    "year": release.year if release else None,
                    "rating": _rating(item.get("vote_average")),
                }
            )
            if len(results) >= limit:
                break

        return results

    async def _search_titles(
        self, path: str, title_key: str, date_key: str, query: str
    ) -> list[dict]:
        query = query.strip()
        if not query:
            return []

        async with self._session() as session:
            payload = await self._get(
                session, path, {"query": query, "include_adult": "false"}
            )

        results = []
        for item in payload.get("results", []):
            release = _parse_date(item.get(date_key))
            results.append(
                {
                    "tmdb_id": item["id"],
                    "title": item.get(title_key) or "",
                    "year": release.year if release else None,
                    "popularity": item.get("popularity") or 0.0,
                }
            )
        return results

    async def search_movies(self, query: str) -> list[dict]:
        """Every movie matching a title, for offline scoring against a year."""
        return await self._search_titles(
            "/search/movie", "title", "release_date", query
        )

    async def search_shows(self, query: str) -> list[dict]:
        """Every show matching a title, for offline scoring against a year."""
        return await self._search_titles("/search/tv", "name", "first_air_date", query)

    async def get_movie(self, tmdb_id: int) -> dict:
        async with self._session() as session:
            payload = await self._get(session, f"/movie/{tmdb_id}")

        return {
            "tmdb_id": payload["id"],
            "title": payload["title"],
            "release_date": _parse_date(payload.get("release_date")),
            "runtime": payload.get("runtime") or None,
            "rating": _rating(payload.get("vote_average")),
            "status": payload.get("status"),
        }

    async def get_show(self, tmdb_id: int) -> dict:
        """Show-level data plus the season list, without episode detail."""
        async with self._session() as session:
            payload = await self._get(session, f"/tv/{tmdb_id}")

        seasons = [
            {
                "number": season["season_number"],
                "episode_count": season.get("episode_count") or 0,
                "air_date": _parse_date(season.get("air_date")),
            }
            for season in payload.get("seasons", [])
            if season.get("season_number") != SPECIALS_SEASON
        ]

        return {
            "tmdb_id": payload["id"],
            "name": payload["name"],
            "release_date": _parse_date(payload.get("first_air_date")),
            "rating": _rating(payload.get("vote_average")),
            # "Returning Series" is the only status with episodes still to come.
            "status": payload.get("status"),
            "in_production": bool(payload.get("in_production")),
            "seasons": seasons,
            "is_multi_season": len(seasons) > 1,
        }

    async def get_season_episodes(self, tmdb_id: int, season_number: int) -> list[dict]:
        async with self._session() as session:
            return await self._season_episodes(session, tmdb_id, season_number)

    async def _season_episodes(
        self, session: aiohttp.ClientSession, tmdb_id: int, season_number: int
    ) -> list[dict]:
        payload = await self._get(session, f"/tv/{tmdb_id}/season/{season_number}")
        return [
            {
                "number": episode["episode_number"],
                "season_number": season_number,
                "name": episode.get("name") or "",
                "runtime": episode.get("runtime") or None,
                "air_date": _parse_date(episode.get("air_date")),
            }
            for episode in payload.get("episodes", [])
        ]

    async def get_all_seasons(self, show: dict) -> dict[int, list[dict]]:
        """Fetch every season's episodes for a show, concurrently.

        Takes an already-fetched ``show`` so importing a series costs one request
        per season rather than re-reading the show summary each time.
        """
        season_numbers = [season["number"] for season in show["seasons"]]
        if not season_numbers:
            return {}

        async with self._session() as session:
            episode_lists = await asyncio.gather(
                *(
                    self._season_episodes(session, show["tmdb_id"], number)
                    for number in season_numbers
                )
            )

        return dict(zip(season_numbers, episode_lists))
