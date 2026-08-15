"""Rebinds Trakt-era entries onto TMDB ids.

The August 2026 TMDB switch replaced the import and refresh code but left the
documents alone, so every movie and season imported before it still carries
``trakt_id`` metadata. ``refresh-media`` selects on ``tmdb_type``, which those
documents do not have, and has therefore silently skipped all of them since.

A Trakt id cannot be translated directly -- TMDB's /find endpoint does not know
them and Trakt's own API is gone -- so entries are rematched by title and year.
Every proposal is reported before anything is written, and anything short of an
unambiguous match is left for a human rather than guessed at, because a wrong
binding is permanent and silent: the entry would then refresh happily against
the wrong show forever.

Subentries are never touched. This rewrites identity only, so the estimates,
spent time and watched flags stay exactly as they are and the first
``refresh-media`` afterwards fills in the gaps.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
import re
import unicodedata
from typing import Literal

from shelfspace.apis.tmdb import SPECIALS_SEASON, TMDBAPI, season_key
from shelfspace.models import Entry

# "157841_s1" -> show 157841, season 1.
SEASON_ID_RE = re.compile(r"^(\d+)_s(\d+)$")

# Trailing " S3" on a season Entry's name; the show's own title is what TMDB knows.
SEASON_SUFFIX_RE = re.compile(r"\s+S\d+$")

# The year Trakt appends to a slug, e.g. "alice-in-borderland-2020".
SLUG_YEAR_RE = re.compile(r"-(\d{4})$")

# Wide enough for anything with a release date, narrow enough to reject a title
# that merely ends in four digits.
YEAR_RANGE = range(1870, 2101)

# A candidate this many years off the recorded date is a different title.
MAX_YEAR_DRIFT = 1

# Same title, same year, wildly different audience: TMDB carries shorts, fan
# edits and duplicates alongside the real film. This much more popularity is
# taken as "these are not really rivals"; below it the tie is a real one.
POPULARITY_DOMINANCE = 5.0

# Two sources rarely agree to the day on when a season started.
SEASON_DATE_TOLERANCE = timedelta(days=45)

# Trakt and TMDB split seasons differently often enough that a small gap means
# nothing; a large one means this is not the same show.
MAX_EPISODE_COUNT_DRIFT = 3

# Enough to cover the real title plus its impostors, without a request storm.
MAX_SHOW_CANDIDATES = 4

# TMDB allows far more, but a one-off migration has no reason to push it.
MAX_CONCURRENT_LOOKUPS = 8


@dataclass(slots=True)
class Proposal:
    """One rebinding decision: the entries it covers and the match found."""

    kind: Literal["movie", "show"]
    entries: list[Entry]
    title: str
    year: int | None
    match: dict | None = None
    confident: bool = False
    notes: list[str] = field(default_factory=list)
    # Entries this proposal deliberately leaves on their Trakt ids.
    skipped: list[Entry] = field(default_factory=list)

    @property
    def label(self) -> str:
        year = f" ({self.year})" if self.year else ""
        return f"{self.title}{year}"


def _normalize(title: str) -> str:
    """Fold a title down to what two sources can be expected to agree on."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def _slug(entry: Entry) -> str:
    for link in entry.links:
        match = re.search(r"trakt\.tv/(?:movies|shows)/([^/?]+)", link)
        if match:
            return match.group(1)
    return ""


def _slug_year(slug: str, title: str) -> int | None:
    """The year Trakt appended to a slug, unless it is part of the title.

    "blade-runner-2049" ends in a plausible year that is really the name, so the
    digits only count as a year when the title does not already end in them.
    """
    match = SLUG_YEAR_RE.search(slug)
    if not match:
        return None

    year = int(match.group(1))
    if year not in YEAR_RANGE:
        return None
    if _normalize(title).endswith(match.group(1)):
        return None
    return year


def _exact_title_matches(candidates: list[dict], title: str) -> list[dict]:
    wanted = _normalize(title)
    return [c for c in candidates if _normalize(c["title"]) == wanted]


def _dominant(candidates: list[dict]) -> dict | None:
    """The one candidate that outweighs the rest, or None if it is a real tie."""
    ranked = sorted(candidates, key=lambda c: c["popularity"], reverse=True)
    if len(ranked) == 1:
        return ranked[0]
    if ranked[1]["popularity"] <= 0:
        return ranked[0]
    if ranked[0]["popularity"] / ranked[1]["popularity"] >= POPULARITY_DOMINANCE:
        return ranked[0]
    return None


def _pick_movie(
    candidates: list[dict], title: str, year: int | None
) -> tuple[dict | None, bool, str]:
    """Choose a TMDB film, and say whether the choice is safe to apply.

    TMDB lists shorts, fan edits and duplicate records under the same title and
    year as the real film, so a tie on title and year is usually not a genuine
    ambiguity -- but sometimes it is, and two real films sharing both is exactly
    the case that must not be guessed. Popularity separates them: the noise sits
    orders of magnitude below the real record, while two real films do not.
    """
    if not candidates:
        return None, False, "no TMDB result"

    exact = _exact_title_matches(candidates, title)
    if not exact:
        return (
            max(candidates, key=lambda c: c["popularity"]),
            False,
            "no exact title match",
        )

    if year is None:
        best = _dominant(exact)
        if best is None:
            return (
                max(exact, key=lambda c: c["popularity"]),
                False,
                f"{len(exact)} titles match and no year on record",
            )
        return best, True, "unique title match, no year on record"

    dated = [c for c in exact if c["year"] is not None]
    close = [c for c in dated if abs(c["year"] - year) <= MAX_YEAR_DRIFT]
    if not close:
        years = ", ".join(str(c["year"]) for c in dated) or "none dated"
        return (
            max(exact, key=lambda c: c["popularity"]),
            False,
            f"no candidate near {year} (TMDB has {years})",
        )

    # An exact year beats a neighbouring one outright; the tolerance only exists
    # to survive a release that slipped across New Year between the two sources.
    same_year = [c for c in close if c["year"] == year]
    finalists = same_year or close

    best = _dominant(finalists)
    if best is None:
        return (
            max(finalists, key=lambda c: c["popularity"]),
            False,
            f"{len(finalists)} comparable candidates around {year}",
        )
    return best, True, "title and year match"


async def _propose_movie(api: TMDBAPI, entry: Entry) -> Proposal:
    """Match a movie on its own name and release date, not its Trakt slug.

    The stored name and date came from Trakt's own record of the film, so they
    are cleaner inputs than a slug that has to be picked apart.
    """
    title = entry.name
    year = entry.release_date.year if entry.release_date else None
    if year is None:
        year = _slug_year(_slug(entry), title)

    proposal = Proposal(kind="movie", entries=[entry], title=title, year=year)
    candidates = await api.search_movies(title)
    proposal.match, proposal.confident, reason = _pick_movie(candidates, title, year)
    proposal.notes.append(reason)
    return proposal


def _dates_agree(ours: date | None, theirs: date | None) -> bool:
    if ours is None or theirs is None:
        return False
    return abs(ours - theirs) <= SEASON_DATE_TOLERANCE


def _counts_agree(ours: int, theirs: int | None) -> bool:
    if not theirs:
        return False
    return abs(ours - theirs) <= MAX_EPISODE_COUNT_DRIFT


def _score_show(show: dict, entries: list[Entry]) -> tuple[int, int, int]:
    """How well a candidate explains the seasons already tracked here.

    Ranked by air-date agreement first: a season premiere is a far sharper
    fingerprint than a title, and it is what separates the show actually being
    tracked from something else released the same year under the same name.
    """
    available = {season["number"]: season for season in show["seasons"]}
    dated = sized = present = 0

    for entry in entries:
        season = available.get(_season_number(entry))
        if season is None:
            continue
        present += 1
        dated += _dates_agree(entry.release_date, season["air_date"])
        sized += _counts_agree(len(entry.subentries), season["episode_count"])

    return dated, sized, present


async def _propose_show(api: TMDBAPI, entries: list[Entry]) -> Proposal:
    """Match a show once, covering every tracked season of it at the same time.

    A season Entry's release_date is its own first episode, so the show's year
    has to come from the slug -- and the slug frequently has none. Rather than
    lean on a year that may not exist, candidates are judged on whether their
    season list actually lines up with the seasons already in the database.
    """
    entries.sort(key=lambda e: _season_number(e) or 0)
    title = SEASON_SUFFIX_RE.sub("", entries[0].name)
    year = _slug_year(_slug(entries[0]), title)

    # TMDB drops season 0, so a tracked specials season has nothing to bind to.
    specials = [e for e in entries if _season_number(e) in (None, SPECIALS_SEASON)]
    bindable = [e for e in entries if e not in specials]

    proposal = Proposal(
        kind="show", entries=bindable, title=title, year=year, skipped=specials
    )
    if specials:
        proposal.notes.append(
            f"{len(specials)} specials season(s) left on Trakt ids - "
            "TMDB does not track season 0"
        )
    if not bindable:
        return proposal

    candidates = await api.search_shows(title)
    if not candidates:
        proposal.notes.append("no TMDB result")
        return proposal

    pool = _exact_title_matches(candidates, title) or candidates
    if year:
        near = [
            c for c in pool if c["year"] and abs(c["year"] - year) <= MAX_YEAR_DRIFT
        ]
        pool = near or pool
    shortlist = sorted(pool, key=lambda c: c["popularity"], reverse=True)[
        :MAX_SHOW_CANDIDATES
    ]

    shows = await asyncio.gather(*(api.get_show(c["tmdb_id"]) for c in shortlist))
    fits = [
        (_score_show(show, bindable), candidate, show)
        for candidate, show in zip(shortlist, shows)
    ]
    fits.sort(key=lambda fit: (fit[0], fit[1]["popularity"]), reverse=True)

    (dated, sized, present), best, show = fits[0]
    proposal.match = best
    proposal.notes.append(
        f"{present}/{len(bindable)} tracked seasons found, {dated} agreeing on air date"
    )

    available = {season["number"]: season for season in show["seasons"]}
    for entry in bindable:
        number = _season_number(entry)
        season = available.get(number)
        if season is None:
            proposal.notes.append(f"S{number} missing from this show on TMDB")
        elif not _counts_agree(len(entry.subentries), season["episode_count"]):
            proposal.notes.append(
                f"S{number} has {season['episode_count']} episodes on TMDB, "
                f"{len(entry.subentries)} here"
            )

    if present < len(bindable):
        return proposal

    if len(fits) > 1 and fits[1][0] >= (dated, sized, present):
        proposal.notes.append("another show on TMDB fits these seasons just as well")
        return proposal

    # Nothing lined up on either date or episode count, so the only thing
    # actually matched was the title -- which is what got us here.
    if not dated and not sized:
        proposal.notes.append("no air date or episode count agreement to confirm it")
        return proposal

    proposal.confident = True
    return proposal

    return proposal


def _season_number(entry: Entry) -> int | None:
    match = SEASON_ID_RE.match(str(entry.metadata.get("trakt_id", "")))
    return int(match.group(2)) if match else None


async def collect_proposals(api: TMDBAPI) -> list[Proposal]:
    """Work out a TMDB binding for every entry still on a Trakt id.

    Entries already carrying ``tmdb_type`` are skipped, so this can be run again
    to pick up whatever was left unresolved last time.
    """
    entries = await Entry.find(
        {
            "metadata.trakt_id": {"$exists": True},
            "metadata.tmdb_type": {"$exists": False},
        }
    ).to_list()

    movies = [e for e in entries if "show_trakt_id" not in e.metadata]
    shows: dict[int, list[Entry]] = {}
    for entry in entries:
        show_id = entry.metadata.get("show_trakt_id")
        if show_id is not None:
            shows.setdefault(show_id, []).append(entry)

    limit = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

    async def guarded(coro):
        async with limit:
            return await coro

    return list(
        await asyncio.gather(
            *(guarded(_propose_movie(api, entry)) for entry in movies),
            *(guarded(_propose_show(api, group)) for group in shows.values()),
        )
    )


def rebind(entry: Entry, kind: str, tmdb_id: int) -> None:
    """Give one entry the TMDB identity that import would have given it.

    The keys written here are exactly what ``build_movie_entry`` and
    ``build_season_entry`` write, because ``refresh-media`` cannot tell a
    rebound entry from a freshly imported one and must not need to.

    The old ``trakt_id`` keys are left in place: nothing reads them any more,
    and keeping them shows what an entry was rebound from.
    """
    if kind == "movie":
        entry.metadata |= {"tmdb_id": tmdb_id, "tmdb_type": "movie"}
        url = f"https://www.themoviedb.org/movie/{tmdb_id}"
    else:
        number = _season_number(entry)
        entry.metadata |= {
            "tmdb_id": season_key(tmdb_id, number),
            "tmdb_show_id": tmdb_id,
            "tmdb_season": number,
            "tmdb_type": "season",
        }
        url = f"https://www.themoviedb.org/tv/{tmdb_id}/season/{number}"

    # The Trakt link is dead; anything else on the entry was put there by hand.
    links = [link for link in entry.links if "trakt.tv" not in link]
    if url not in links:
        links.insert(0, url)
    entry.links = links


async def apply_proposal(proposal: Proposal) -> None:
    """Write a confident proposal's TMDB identity onto its entries."""
    if proposal.match is None:
        raise ValueError(f"{proposal.label} has no match to apply")

    for entry in proposal.entries:
        rebind(entry, proposal.kind, proposal.match["tmdb_id"])
        await entry.save()
