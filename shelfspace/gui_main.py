from nicegui import app, ui

from shelfspace.app_ctx import AppCtx
from shelfspace.models import Entry
from shelfspace.utils import format_minutes


# Define required shelves that should always be shown
REQUIRED_SHELVES = ["Backlog", "Icebox"]


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

    # Ensure required shelves are present (even if empty)
    for shelf in REQUIRED_SHELVES:
        if shelf not in entries_by_shelf:
            entries_by_shelf[shelf] = []

    return entries_by_shelf


async def update_entry_shelf(entry_id: str, new_shelf: str, refresh_func) -> None:
    """Update an entry's shelf and refresh the display."""
    entry_obj = await Entry.get(entry_id)
    if entry_obj:
        entry_obj.shelf = new_shelf if new_shelf != "Uncategorized" else ""
        await entry_obj.save()
        await refresh_func()


def get_all_shelves() -> list[str]:
    """Get all possible shelf options."""
    return sorted(REQUIRED_SHELVES + ["Uncategorized"])


def create_entry_card(entry: Entry, refresh_func) -> None:
    """Create a card for an entry with shelf selector."""
    with ui.card().classes("w-full").props('draggable="true"'):
        with ui.row().classes("w-full items-start justify-between"):
            with ui.column().classes("flex-1 gap-2"):
                emoji = get_emoji_for_type(entry.type)
                year = entry.release_date.year if entry.release_date else "N/A"
                estimated_formatted = (
                    format_minutes(entry.estimated) if entry.estimated else "N/A"
                )
                spent_formatted = format_minutes(entry.spent) if entry.spent else "—"
                status_str = entry.status.value if entry.status else "—"
                rating_str = f"⭐ {entry.rating}" if entry.rating else "—"

                ui.label(f"{emoji} {entry.name}").classes("text-lg font-semibold")
                with ui.row().classes("gap-4 text-sm flex-wrap"):
                    ui.label(f"Type: {entry.type.value}")
                    ui.label(f"Year: {year}")
                    ui.label(f"Est: {estimated_formatted}")
                    ui.label(f"Spent: {spent_formatted}")
                    ui.label(f"Status: {status_str}")
                    ui.label(rating_str)
                if entry.notes:
                    ui.label(f"Notes: {entry.notes}").classes(
                        "text-sm text-gray-600 italic"
                    )

            # Shelf selector dropdown
            with ui.column().classes("ml-4 items-end"):
                current_shelf = entry.shelf or "Uncategorized"
                shelf_select = ui.select(
                    options=get_all_shelves(),
                    value=current_shelf,
                ).props("dense outlined")

                async def on_shelf_changed(new_val: str):
                    await update_entry_shelf(str(entry.id), new_val, refresh_func)

                shelf_select.on_value_change(on_shelf_changed)


def create_shelf_section(shelf: str, entries: list[Entry], refresh_func) -> None:
    """Create a section for a shelf with all its entries."""
    entry_count = len(entries)
    with ui.column().classes("w-full gap-2"):
        ui.label(f"📚 {shelf} ({entry_count})").classes("text-xl font-semibold")

        if not entries:
            ui.label("No entries yet").classes("text-center text-gray-400 py-4 italic")
        else:
            with ui.column().classes(
                "w-full p-3 border-2 border-gray-300 rounded-lg bg-gray-50 gap-2"
            ):
                for entry in sorted(entries, key=lambda e: e.name):
                    create_entry_card(entry, refresh_func)


async def setup_ui():
    """Setup the main UI with all entries grouped by shelf."""
    entries_by_shelf = await load_entries()

    async def refresh_entries():
        """Refresh the entries display."""
        # Trigger re-render by reloading the page
        ui.run_javascript("location.reload();")

    # Add drag-and-drop styles and scripts
    ui.add_head_html(
        """
        <style>
        [draggable="true"] {
            cursor: grab;
            opacity: 1;
            transition: opacity 0.2s;
        }
        [draggable="true"]:active {
            cursor: grabbing;
            opacity: 0.7;
        }
        </style>
        """
    )

    with ui.column().classes("w-full max-w-6xl mx-auto p-4"):
        ui.label("📚 All Entries").classes("text-3xl font-bold mb-6")

        if not entries_by_shelf:
            ui.label("No entries found.").classes("text-lg text-gray-500")
        else:
            # Sort shelves with required shelves first
            shelves = sorted(entries_by_shelf.keys())
            sorted_shelves = sorted(
                shelves,
                key=lambda x: (
                    x not in REQUIRED_SHELVES,
                    REQUIRED_SHELVES.index(x) if x in REQUIRED_SHELVES else 999,
                    x,
                ),
            )

            for shelf in sorted_shelves:
                shelf_entries = entries_by_shelf[shelf]
                create_shelf_section(shelf, shelf_entries, refresh_entries)


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
