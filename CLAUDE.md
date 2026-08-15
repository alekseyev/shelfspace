# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shelfspace is a media aggregation and management system for tracking reading/viewing/play lists. It aggregates content from multiple APIs (TMDB, Goodreads, HowLongToBeat, Steam) and organizes them into shelves for planning and tracking media consumption.

## Development Commands

### Environment Setup
```bash
# Install dependencies and setup environment
make install

# Format code with ruff
make format

# Run the GUI application
make run
```

### Database Setup
```bash
# Start MongoDB and mongo-express via Docker
docker-compose up -d

# MongoDB is available at localhost:4001
# Mongo Express (web UI) is available at localhost:4002 (admin:pass)
```

### CLI Commands (via shelf.py)
```bash
# Process and import content from external APIs
python shelf.py refresh-media       # Refresh movies/shows from TMDB (new episodes, runtimes, air dates, ratings)
python shelf.py process-games       # Import games from HowLongToBeat
python shelf.py process-books       # Import books from Goodreads
python shelf.py sync-steam-playtime # Credit Steam playtime to the current shelf
python shelf.py migrate-to-tmdb     # Rebind pre-TMDB entries onto TMDB ids (dry run without --apply)

# List all entries
python shelf.py list-entries

# Back up / restore the database as JSON
python shelf.py export-data
python shelf.py import-data
```

## Architecture

### Core Data Model (models.py)

The application uses a hierarchical document model stored in MongoDB via Beanie ODM:

**Entry** - Top-level media item (movie, book, game, etc.)
- Contains metadata, type, name, release date, rating, links
- Has a list of SubEntries representing portions of the content
- Each Entry can span multiple shelves via its SubEntries

**SubEntry** - Subdivision of an Entry (e.g., episode, chapter, or the whole item)
- References a Shelf via `shelf_id` (ObjectId reference)
- Contains estimated time, spent time, completion status
- Has optional name (e.g., "S01E01" for TV shows)
- One Entry typically has multiple SubEntries if it's episodic content
- Single-unit content (movies, books) typically has one SubEntry

**Shelf** - Organization container (sprint/time period)
- Has name, start/end dates, weight for sorting
- Default shelves: "Icebox" (future), "Backlog" (near-term), "Upcoming"
- Named shelves can represent time periods (e.g., "15 January - 22 January 2025")

**Key Pattern**: TV shows create one Entry per season. Multi-season shows get Entry names like "Show Name S2", while single-season shows are just "Show Name". Each episode is a SubEntry.

### Application Structure

**GUI Application** (`gui_main.py`)
- NiceGUI-based web interface for managing entries
- Drag-and-drop interface for moving SubEntries between shelves
- Main view groups SubEntries by shelf
- Uses FastAPI backend with NiceGUI frontend
- Requires MongoDB connection (configured via settings)

**CLI Application** (`shelf.py`)
- AsyncTyper-based CLI for bulk operations
- Commands to import from external APIs
- Commands to list and view entries

**API Integrations** (`shelfspace/apis/`)
- `tmdb.py` - TMDBAPI for movies and TV shows, plus the Entry builders and refresh rules
- `hltb.py` - HowLongAPI for game time estimates (uses Playwright for scraping)
- `goodreads.py` - GoodreadsAPI for books (reads the public shelf RSS feed, no auth)
- `steam.py` - SteamAPI for owned games and playtime

**Media Library** (`library.py`, `shelving.py`)
- `library.py` - import/refresh operations shared by the GUI add dialog and `refresh-media`
- `shelving.py` - `ShelfPlacement`, which decides the shelf for an unwatched item from its air date. Placement is **forward-only**: see the re-shelving rules under TMDB below

**Time Estimations** (`estimations.py`)
- Functions to estimate completion time for different media types
- `estimate_book_from_pages()` - Regular books (2.5 min/page)
- `estimate_ed_book_from_pages()` - Educational books (5 min/page)
- `estimate_comic_book_from_pages()` - Comics (1 min/page)
- `round_up_game_estimate()` - Rounds game hours to intervals

**Application Context** (`app_ctx.py`)
- Manages MongoDB connection lifecycle
- AppCtx class handles Beanie initialization
- Used by both GUI and CLI applications

### Configuration

**Settings** (`settings.py`)
- Environment variables with `SET_` prefix
- MongoDB connection: `SET_MONGO_URL`, `SET_MONGO_DB`
- API credentials: `SET_TMDB_TOKEN`, `SET_HLTB_USER`, `SET_GOODREADS_USER`, `SET_STEAM_API_KEY`, `SET_STEAM_USER_ID`
- Uses pydantic-settings for configuration management
- Note `settings.py` sets no `env_file`, so a `.env` is *not* read — values come from the shell (`.envrc` via direnv)

`SET_TMDB_TOKEN` is a TMDB read access token (bearer), free for personal use from
https://www.themoviedb.org/settings/api. Every integration now authenticates from
the environment, so there is no longer a `secrets.json` or a module managing it.

### Important Implementation Notes

1. **Shelf References**: Shelves are referenced by `shelf_id` (ObjectId). `SubEntry.shelf` (string name) is a leftover of the older scheme, kept only so old exports still round-trip through `import-data`; nothing reads it.

2. **TMDB API**: Replaced Trakt in August 2026, after Trakt stopped recognising the app's client ID and gated new apps behind VIP. TMDB was chosen over Simkl because it reports a runtime *per episode* — Simkl only has it at show level, which would flatten every episode estimate to a show-wide average.

   Movies and shows are added from the GUI (`save_from_tmdb` in `gui_main.py` → `library.import_movie` / `import_series`). Picking a show imports **every** season, one Entry per season, with a SubEntry per episode. Episodes are placed by air date, except when Icebox is chosen, which parks the whole show there.

   `refresh-media` keeps them current: newly scheduled episodes, whole new seasons for shows already tracked, slipped air dates, runtimes unknown at import, and rating drift. It then re-shelves every unwatched episode by air date, so a delayed episode follows itself onto the right sprint. **Finished subentries are never touched** — they record what was actually watched, not a prediction. A fully watched season of an ended show is skipped entirely.

   New seasons are only added *forward*: `should_add_season` in `library.py` skips any season numbered below the earliest one already tracked for that show. Picking a show up at its current season is normal, and without this a refresh drags the whole back catalogue into the Backlog.

   **Re-shelving is forward-only too** (`ShelfPlacement._is_forward`). An air date is a prediction of when something *can* be watched, not an instruction about when it must be, so an episode only ever moves to a *later* shelf. Two cases this protects: a season kept together on the current sprint even though its early episodes aired weeks ago, and episodes deliberately pushed out to be binged once the season finishes. Backlog and Icebox sit outside the calendar rather than later in it, so an item on a dated shelf is never demoted to one.

   **The Icebox is a hold, not a bin.** `reassign` never takes an episode out of it and never puts one in. The three shelves mean different things: a dated shelf is "planned for then", Backlog is "soon, unscheduled", Icebox is "not until I say so". Only `is_parked` writes to the Icebox, and only for episodes that have no shelf yet — a season invented by a refresh, which must not be scheduled ahead of seasons still waiting. Parking carries **forward** across seasons and never back (`parked_at` in `library.py`): a show with S2 iced and S1 in the Backlog is the ordinary "don't watch S2 before S1" case, and S1 must stay put. Sweeping the whole show into the Icebox because one season was iced was a real bug.

   `ShelfPlacement` takes **all** shelves (`Shelf.get_all_dict()`), finished ones included, because it has to tell "aired during a sprint that has since closed" from "never scheduled". It still never *schedules* onto a finished shelf: `self.dated` filters them out, so a past air date resolves to Backlog. `Shelf.get_shelves_dict()` remains the open-shelves display cache and must not be used for placement — that was the Lucky bug, where episodes that aired inside a closed sprint fell through to Backlog and were dragged off the current shelf.

   `migrate-to-tmdb` is the one-off that rebound entries imported before the TMDB switch, which still carried `trakt_id` metadata and were therefore invisible to `refresh-media` (it selects on `tmdb_type`). Trakt ids cannot be translated — TMDB's `/find` does not know them and Trakt's API is gone — so `migration.py` rematches by title and year, and refuses anything ambiguous rather than guessing, because a wrong binding is silent and permanent. Movies are separated by TMDB popularity when a title and year tie (shorts and duplicates sit orders of magnitude below the real film; two real films do not). Shows are matched on season *air dates*, a far sharper fingerprint than a title — this is what distinguishes the 2013 thriller *Utopia* from the soap of the same name. Five entries remain on Trakt ids and need a decision by hand; season 0 (specials) has no TMDB counterpart at all.

3. **Web Scraping**: The HLTB API uses Playwright for scraping. The `browser.py` module provides shared browser context management.

   Goodreads does not: it reads `review/list_rss/<user>?shelf=to-read`, which needs no login and no browser but requires the shelf to be public. The feed reports a publication *year* only, so book `release_date` is stored as 1 January of that year. Book media type comes from the user's own Goodreads shelves — `want-to-read-comics` maps to `Book (comics)` and `want-to-read-tech` to `Book (educational)` (see `COMICS_SHELF` / `EDUCATIONAL_SHELF` in `goodreads.py`).

   Those two genre shelves are fetched as feeds of their own and a book is classified by which feed it appears in, *not* by the `user_shelves` field inside the to-read feed. Goodreads caches each shelf's feed separately, and the to-read one went on describing two freshly re-shelved comics as plain `to-read` long after the comics feed had picked them up — so reading the field meant a re-shelved book kept its old media type and reading rate until the cache happened to expire. Membership in a genre feed is a fact about the response rather than a field inside it, so it cannot go stale in that way, and it un-classifies as well as it classifies. The merge runs in the other direction too: a book the to-read feed has not caught up on is imported from the genre feed, since otherwise `_cleanup_removed_books` would read the omission as "removed from Goodreads" and delete the entry.

4. **Deduplication**: Import commands check for existing entries by API-specific IDs (e.g., `tmdb_id`, `hltb_id`, `goodreads_id`) stored in the `metadata` field. TMDB entries carry `tmdb_type` (`"movie"` or `"season"`); a season's `tmdb_id` is `"<show_id>_s<n>"` and it also stores `tmdb_show_id` and `tmdb_season`.

   `process-books` goes further and refreshes entries it has already imported (`_refresh_book_entry`), because Goodreads fills in page counts and firm release dates for unreleased books late and the community rating drifts. It refreshes type, rating, release year, page count and estimate. Two rules keep it from destroying better data: it stores the page count it last saw as `metadata["goodreads_pages"]` and only recomputes an estimate that still matches what that page count implies (so a hand-tuned estimate survives), and it only touches `release_date` when the *year* differs (so an exact date captured by the old scraper is not downgraded to 1 January).

5. **Time Format**: All time estimates are stored in minutes (int). Use `format_minutes()` from `utils.py` for human-readable display.

6. **Watched Status**: Set by hand via the "Mark as watched" (eye) button on movie cards and episode rows — there is no watch-history sync any more. `SubEntry.mark_watched()` holds the transition; the handler only notifies and refreshes.

## Testing and Development

- The codebase uses Python 3.14+ (see `.python-version`)
- Uses `uv` for dependency management
- Ruff for linting and formatting
- Playwright requires installation: `playwright install`
- MongoDB must be running before using the application
- `uv run pytest` — tests live in `tests/`, config in `pytest.ini`. They need MongoDB up (Beanie will not build a Document without an initialised collection) but write nothing to it.
