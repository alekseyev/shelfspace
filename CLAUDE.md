# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shelfspace is a media aggregation and management system for tracking reading/viewing/play lists. It aggregates content from multiple APIs (Goodreads, Trakt, HowLongToBeat) and organizes them into shelves for planning and tracking media consumption.

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
python shelf.py process-movies      # Import movies from Trakt
python shelf.py process-shows       # Import TV shows from Trakt
python shelf.py process-upcoming    # Add upcoming episodes from Trakt calendar
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
- `trakt.py` - TraktAPI for movies and TV shows (requires OAuth tokens)
- `hltb.py` - HowLongAPI for game time estimates (uses Playwright for scraping)
- `goodreads.py` - GoodreadsAPI for books (uses Playwright for scraping)
- `secrets.py` - Manages API credentials from secrets.json

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
- API credentials: `SET_TRAKT_CLIENT_ID`, `SET_TRAKT_CLIENT_SECRET`, `SET_HLTB_USER`, `SET_GOODREADS_USER`
- Uses pydantic-settings for configuration management

**Secrets** (`secrets.json`)
- Stores Trakt OAuth tokens (access_token, refresh_token)
- Managed via `shelfspace/apis/secrets.py`
- Not committed to git

### Important Implementation Notes

1. **Shelf References**: The codebase is transitioning from string-based shelf names to ObjectId references. New code uses `shelf_id` (ObjectId), but legacy code may still use `shelf` (string). The models support both during migration.

2. **Trakt API**: Uses OAuth2 with token refresh. The `TraktAPI` class automatically refreshes tokens when needed and saves them back via `save_trakt_secrets()`.

3. **Web Scraping**: Goodreads and HLTB APIs use Playwright for scraping. The `browser.py` module provides shared browser context management.

4. **Deduplication**: Import commands check for existing entries by API-specific IDs (e.g., `trakt_id`, `hltb_id`, `goodreads_id`) stored in the `metadata` field.

5. **Time Format**: All time estimates are stored in minutes (int). Use `format_minutes()` from `utils.py` for human-readable display.

6. **Legacy Code**: `main.py` and `*_old.py` files contain legacy code for Notion integration. The new architecture uses MongoDB directly.

## Testing and Development

- The codebase uses Python 3.14+ (see `.python-version`)
- Uses `uv` for dependency management
- Ruff for linting and formatting
- Playwright requires installation: `playwright install`
- MongoDB must be running before using the application
