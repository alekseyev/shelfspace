from nicegui import app, ui

from shelfspace.app_ctx import AppCtx
from shelfspace.models import Entry
from shelfspace.utils import format_minutes


def get_emoji_for_type(media_type):
    """Get emoji representation for media type."""
    emoji_map = {
        "Projects": "🏗️",
        "Duolingo": "🗣️",
        "Course": "📚",
        "Movie": "🎬",
        "Series": "📺",
        "Game": "🎮",
        "Game (VR)": "🥽",
        "Game (mobile)": "📱",
        "Book": "📖",
        "Book (educational)": "📚",
        "Book (comics)": "💭",
        "Article": "📰",
        "Talk/video": "🎥",
    }
    return emoji_map.get(media_type, "📌")


async def load_entries():
    """Load all entries from the database grouped by shelf."""
    await AppCtx.ensure_initialized()
    entries = await Entry.find().to_list()

    # Group entries by shelf
    entries_by_shelf = {}
    for entry in entries:
        shelf = entry.shelf or "Uncategorized"
        if shelf not in entries_by_shelf:
            entries_by_shelf[shelf] = []
        entries_by_shelf[shelf].append(entry)

    return entries_by_shelf


def create_entries_table(entries: list[Entry]) -> None:
    """Create a table for entries."""
    rows = []
    for entry in sorted(entries, key=lambda e: e.name):
        emoji = get_emoji_for_type(entry.type)
        year = entry.release_date.year if entry.release_date else "N/A"
        estimated_formatted = (
            format_minutes(entry.estimated) if entry.estimated else "N/A"
        )
        spent_formatted = format_minutes(entry.spent) if entry.spent else "—"
        rows.append(
            {
                "": emoji,
                "Name": entry.name,
                "Type": entry.type.value,
                "Year": year,
                "Estimated": estimated_formatted,
                "Spent": spent_formatted,
                "Status": entry.status.value if entry.status else "—",
                "Rating": f"⭐ {entry.rating}" if entry.rating else "—",
                "Notes": entry.notes if entry.notes else "—",
            }
        )

    ui.table(
        columns=[
            {"name": col, "label": col, "field": col, "align": "left"}
            for col in [
                "",
                "Name",
                "Type",
                "Year",
                "Estimated",
                "Spent",
                "Status",
                "Rating",
                "Notes",
            ]
        ],
        rows=rows,
    ).classes("w-full")


async def setup_ui():
    """Setup the main UI with all entries grouped by shelf."""
    entries_by_shelf = await load_entries()

    with ui.column().classes("w-full mx-auto p-4"):
        ui.label("📚 All Entries").classes("text-3xl font-bold mb-4")

        if not entries_by_shelf:
            ui.label("No entries found.").classes("text-lg text-gray-500")
        else:
            for shelf in sorted(entries_by_shelf.keys()):
                shelf_entries = entries_by_shelf[shelf]
                entry_count = len(shelf_entries)
                ui.label(f"📚 {shelf} ({entry_count})").classes(
                    "text-xl font-semibold mt-6 mb-2"
                )
                create_entries_table(shelf_entries)


# Create the page
@ui.page("/")
async def main_page():
    """Main page showing all entries grouped by shelf."""
    ui.page_title("Shelfspace - Entries")
    await setup_ui()


# Initialize on startup
@app.on_startup
async def on_startup():
    await AppCtx.start()


@app.on_shutdown
async def on_shutdown():
    await AppCtx.shutdown()


ui.run()
