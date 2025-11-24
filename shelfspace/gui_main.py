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


def get_all_shelves() -> list[str]:
    """Get all possible shelf options."""
    return sorted(REQUIRED_SHELVES + ["Uncategorized"])


async def update_entry_shelf(entry_id: str, new_shelf: str, shelves_ui: dict) -> None:
    """Update an entry's shelf and refresh affected shelf containers."""
    entry_obj = await Entry.get(entry_id)
    if not entry_obj:
        return

    old_shelf = entry_obj.shelf or "Uncategorized"
    entry_obj.shelf = new_shelf if new_shelf != "Uncategorized" else ""
    await entry_obj.save()

    # Reload entries to update UI
    entries_by_shelf = await load_entries()

    # Update both old and new shelf containers if they exist
    shelves_to_update = {old_shelf, new_shelf}
    for shelf in shelves_to_update:
        if shelf in shelves_ui:
            container_ref = shelves_ui[shelf]
            # Clear and rebuild the container
            container_ref.clear()
            shelf_entries = entries_by_shelf.get(shelf, [])
            build_shelf_content(shelf, shelf_entries, shelves_ui, container_ref)


def build_shelf_content(
    shelf: str, entries: list[Entry], shelves_ui: dict, container
) -> None:
    """Build the content for a shelf container."""
    entry_count = len(entries)
    with container:
        ui.label(f"📚 {shelf} ({entry_count})").classes("text-xl font-semibold")

        if not entries:
            ui.label("No entries yet").classes("text-center text-gray-400 py-4 italic")
        else:
            with ui.column().classes(
                "w-full p-3 border-2 border-gray-300 rounded-lg bg-gray-50 gap-2"
            ):
                for entry in sorted(entries, key=lambda e: e.name):
                    create_entry_card(entry, shelves_ui)


def create_entry_card(entry: Entry, shelves_ui: dict) -> None:
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

                async def on_shelf_selected(new_shelf: str):
                    await update_entry_shelf(str(entry.id), new_shelf, shelves_ui)

                ui.select(
                    options=get_all_shelves(),
                    value=current_shelf,
                    on_change=on_shelf_selected,
                ).props("dense outlined")


async def setup_ui():
    """Setup the main UI with all entries grouped by shelf."""
    entries_by_shelf = await load_entries()

    # Add drag-and-drop styles
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

            # Create containers for each shelf that we can update
            shelves_ui = {}
            for shelf in sorted_shelves:
                shelf_entries = entries_by_shelf[shelf]
                container = ui.column().classes("w-full gap-2")
                shelves_ui[shelf] = container
                build_shelf_content(shelf, shelf_entries, shelves_ui, container)


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
