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

# List all entries
python shelf.py list-entries
```

### Legacy Commands (main.py)
The `main.py` file contains older commands that export to Notion. These are being migrated to the new system but may still be useful:
- `list-books`, `process-books-csv`, `list-games`, `process-games`, etc.

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
- `base.py` - BaseAPI class with common HTTP methods
- `tmdb.py` - TMDBAPI for movies and TV shows, plus the Entry builders and refresh rules
- `hltb.py` - HowLongAPI for game time estimates (uses Playwright for scraping)
- `goodreads.py` - GoodreadsAPI for books (reads the public shelf RSS feed, no auth)

**Media Library** (`library.py`, `shelving.py`)
- `library.py` - import/refresh operations shared by the GUI add dialog and `refresh-media`
- `shelving.py` - `ShelfPlacement`, which decides the shelf for an unwatched item from its air date

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

1. **Shelf References**: The codebase is transitioning from string-based shelf names to ObjectId references. New code uses `shelf_id` (ObjectId), but legacy code may still use `shelf` (string). The models support both during migration.

2. **TMDB API**: Replaced Trakt in August 2026, after Trakt stopped recognising the app's client ID and gated new apps behind VIP. TMDB was chosen over Simkl because it reports a runtime *per episode* — Simkl only has it at show level, which would flatten every episode estimate to a show-wide average.

   Movies and shows are added from the GUI (`save_from_tmdb` in `gui_main.py` → `library.import_movie` / `import_series`). Picking a show imports **every** season, one Entry per season, with a SubEntry per episode. Episodes are placed by air date, except when Icebox is chosen, which parks the whole show there.

   `refresh-media` keeps them current: newly scheduled episodes, whole new seasons for shows already tracked, slipped air dates, runtimes unknown at import, and rating drift. It then re-shelves every unwatched episode by air date, so a delayed episode follows itself onto the right sprint. **Finished subentries are never touched** — they record what was actually watched, not a prediction. A fully watched season of an ended show is skipped entirely.

3. **Web Scraping**: The HLTB API uses Playwright for scraping. The `browser.py` module provides shared browser context management.

   Goodreads does not: it reads `review/list_rss/<user>?shelf=to-read`, which needs no login and no browser but requires the shelf to be public. The feed reports a publication *year* only, so book `release_date` is stored as 1 January of that year. Book media type comes from the user's own Goodreads shelves — `want-to-read-comics` maps to `Book (comics)` and `want-to-read-tech` to `Book (educational)` (see `COMICS_SHELF` / `EDUCATIONAL_SHELF` in `goodreads.py`).

4. **Deduplication**: Import commands check for existing entries by API-specific IDs (e.g., `tmdb_id`, `hltb_id`, `goodreads_id`) stored in the `metadata` field. TMDB entries carry `tmdb_type` (`"movie"` or `"season"`); a season's `tmdb_id` is `"<show_id>_s<n>"` and it also stores `tmdb_show_id` and `tmdb_season`.

   `process-books` goes further and refreshes entries it has already imported (`_refresh_book_entry`), because Goodreads fills in page counts and firm release dates for unreleased books late and the community rating drifts. It refreshes type, rating, release year, page count and estimate. Two rules keep it from destroying better data: it stores the page count it last saw as `metadata["goodreads_pages"]` and only recomputes an estimate that still matches what that page count implies (so a hand-tuned estimate survives), and it only touches `release_date` when the *year* differs (so an exact date captured by the old scraper is not downgraded to 1 January).

5. **Time Format**: All time estimates are stored in minutes (int). Use `format_minutes()` from `utils.py` for human-readable display.

6. **Watched Status**: Set by hand via the "Mark as watched" (eye) button on movie cards and episode rows — there is no watch-history sync any more. `SubEntry.mark_watched()` holds the transition; the handler only notifies and refreshes.

7. **Legacy Code**: `main.py` and `*_old.py` files contain legacy code for Notion integration. The new architecture uses MongoDB directly.

## Testing and Development

- The codebase uses Python 3.14+ (see `.python-version`)
- Uses `uv` for dependency management
- Ruff for linting and formatting
- Playwright requires installation: `playwright install`
- MongoDB must be running before using the application
- `uv run pytest` — tests live in `tests/`, config in `pytest.ini`. They need MongoDB up (Beanie will not build a Document without an initialised collection) but write nothing to it.
