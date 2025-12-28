from fastapi import Request
from nicegui import app, ui
from nicegui.events import ValueChangeEventArguments

from shelfspace.app_ctx import AppCtx
from shelfspace.models import Entry, Shelf, SubEntry
from shelfspace.utils import format_minutes


# Define required shelves that should always be shown
REQUIRED_SHELVES = ["Backlog", "Icebox"]
DEFAULT_SHELF = "Icebox"

# Global reference to shelves_ui for drag-drop handling
_shelves_ui_ref: dict = {}

# Global shelf objects cache
_shelves_by_name: dict[str, Shelf] = {}
_shelves_by_id: dict = {}


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


async def load_shelves() -> None:
    """Load shelves into global cache."""
    global _shelves_by_name, _shelves_by_id
    await AppCtx.ensure_initialized()

    # Load shelves using the Shelf model's helper to enable shelf_name property
    _shelves_by_id = await Shelf.get_shelves_dict()

    # Also build name -> shelf mapping
    _shelves_by_name = {
        shelf.name: shelf for shelf in _shelves_by_id.values() if not shelf.is_finished
    }


async def load_subentries() -> dict[str, list[tuple[Entry, SubEntry]]]:
    """
    Load all subentries from the database grouped by shelf.

    Returns a dict mapping shelf names to lists of (entry, subentry) tuples.
    """
    await load_shelves()
    entries = await Entry.find().to_list()

    # Group subentries by shelf name (resolved via shelf_id)
    subentries_by_shelf: dict[str, list[tuple[Entry, SubEntry]]] = {}
    for entry in entries:
        for subentry in entry.subentries:
            shelf_name = subentry.shelf_name
            if shelf_name not in subentries_by_shelf:
                subentries_by_shelf[shelf_name] = []
            subentries_by_shelf[shelf_name].append((entry, subentry))

    # Ensure required shelves are present (even if empty)
    for shelf in REQUIRED_SHELVES:
        if shelf not in subentries_by_shelf:
            subentries_by_shelf[shelf] = []

    return subentries_by_shelf


def get_all_shelves() -> list[str]:
    """Get all possible shelf options sorted by weight."""
    global _shelves_by_name
    shelves = list(_shelves_by_name.values())
    # Sort by weight (lower weight comes first)
    shelves.sort(key=lambda s: s.weight)
    return [shelf.name for shelf in shelves]


async def update_subentry_shelf(
    entry_id: str, subentry_name: str, new_shelf_name: str, shelves_ui: dict
) -> None:
    """Update a subentry's shelf and refresh affected shelf containers."""
    global _shelves_by_name

    entry_obj = await Entry.get(entry_id)
    if not entry_obj:
        return

    # Find the subentry and update its shelf
    subentry = None
    for se in entry_obj.subentries:
        if se.name == subentry_name:
            subentry = se
            break

    if not subentry:
        return

    old_shelf_name = subentry.shelf_name
    if old_shelf_name == new_shelf_name:
        return  # No change needed

    # Get the new shelf object and set shelf_id
    new_shelf = _shelves_by_name.get(new_shelf_name)
    if not new_shelf:
        return

    subentry.shelf_id = new_shelf.id
    await entry_obj.save()

    # Reload subentries to update UI
    subentries_by_shelf = await load_subentries()

    # Update both old and new shelf containers if they exist
    shelves_to_update = {old_shelf_name, new_shelf_name}
    for shelf in shelves_to_update:
        if shelf in shelves_ui:
            container_ref = shelves_ui[shelf]
            # Clear and rebuild the container
            container_ref.clear()
            shelf_subentries = subentries_by_shelf.get(shelf, [])
            build_shelf_content(shelf, shelf_subentries, shelves_ui, container_ref)


async def update_all_subentries_shelf(
    entry_id: str, current_shelf_name: str, new_shelf_name: str, shelves_ui: dict
) -> None:
    """Update all subentries of an entry on a specific shelf to a new shelf."""
    global _shelves_by_name

    entry_obj = await Entry.get(entry_id)
    if not entry_obj:
        return

    current_shelf = _shelves_by_name.get(current_shelf_name)
    new_shelf = _shelves_by_name.get(new_shelf_name)
    if not current_shelf or not new_shelf:
        return

    if current_shelf.id == new_shelf.id:
        return  # No change needed

    # Update all subentries on the current shelf to the new shelf
    for subentry in entry_obj.subentries:
        if subentry.shelf_id == current_shelf.id:
            subentry.shelf_id = new_shelf.id

    await entry_obj.save()

    # Reload subentries to update UI
    subentries_by_shelf = await load_subentries()

    # Update both old and new shelf containers if they exist
    shelves_to_update = {current_shelf_name, new_shelf_name}
    for shelf in shelves_to_update:
        if shelf in shelves_ui:
            container_ref = shelves_ui[shelf]
            # Clear and rebuild the container
            container_ref.clear()
            shelf_subentries = subentries_by_shelf.get(shelf, [])
            build_shelf_content(shelf, shelf_subentries, shelves_ui, container_ref)


def build_shelf_content(
    shelf: str,
    subentries: list[tuple[Entry, SubEntry]],
    shelves_ui: dict,
    container,
) -> None:
    """Build the content for a shelf container."""
    subentry_count = len(subentries)
    with container:
        ui.label(f"📚 {shelf} ({subentry_count})").classes("text-xl font-semibold")

        # Create the container for this shelf
        shelf_container = ui.column().classes(
            "w-full p-3 border-2 border-gray-300 rounded-lg bg-gray-50 gap-2 min-h-[80px]"
        )

        with shelf_container:
            if not subentries:
                ui.label("No entries").classes(
                    "text-center text-gray-400 py-4 italic w-full"
                )
            else:
                # Group subentries by entry
                entries_map: dict[str, list[SubEntry]] = {}
                entry_objects: dict[str, Entry] = {}
                for entry, subentry in subentries:
                    entry_id = str(entry.id)
                    if entry_id not in entries_map:
                        entries_map[entry_id] = []
                        entry_objects[entry_id] = entry
                    entries_map[entry_id].append(subentry)

                # Sort entries by name
                sorted_entry_ids = sorted(
                    entries_map.keys(), key=lambda eid: entry_objects[eid].name
                )

                # Display each entry
                for entry_id in sorted_entry_ids:
                    entry = entry_objects[entry_id]
                    subs = entries_map[entry_id]

                    # Always show grouped view for TV shows (SERIES type)
                    # For other types, only show grouped if multiple subentries
                    if entry.type.value == "Series" or len(subs) > 1:
                        # Show grouped with entry header
                        create_grouped_entry_card(entry, subs, shelves_ui)
                    else:
                        # Single subentry for non-series: show as individual card
                        create_subentry_card(entry, subs[0], shelves_ui)


def create_subentry_card(entry: Entry, subentry: SubEntry, shelves_ui: dict) -> None:
    """Create a card for a single subentry with shelf selector."""
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-start justify-between"):
            with ui.column().classes("flex-1 gap-2"):
                emoji = get_emoji_for_type(entry.type)
                year = entry.release_date.year if entry.release_date else "N/A"
                subentry_name = subentry.name or entry.name

                estimated_formatted = (
                    format_minutes(subentry.estimated) if subentry.estimated else "N/A"
                )
                spent_formatted = (
                    format_minutes(subentry.spent) if subentry.spent else "—"
                )
                status_str = "Done" if subentry.is_finished else "In Progress"
                rating_str = f"⭐ {entry.rating}" if entry.rating else "—"

                ui.label(f"{emoji} {subentry_name}").classes("text-lg font-semibold")
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
                current_shelf_name = subentry.shelf_name
                entry_id_captured = str(entry.id)
                subentry_name_captured = subentry.name

                # Define callback that captures entry_id and subentry_name separately
                def make_callback(eid: str, sename: str, ui_ref: dict):
                    async def on_shelf_selected(e: ValueChangeEventArguments):
                        await update_subentry_shelf(eid, sename, e.value, ui_ref)

                    return on_shelf_selected

                ui.select(
                    options=get_all_shelves(),
                    value=current_shelf_name,
                    on_change=make_callback(
                        entry_id_captured, subentry_name_captured, shelves_ui
                    ),
                ).props("dense outlined")


def create_grouped_entry_card(
    entry: Entry, subentries: list[SubEntry], shelves_ui: dict
) -> None:
    """Create a card for an entry with multiple subentries grouped together."""
    entry_id = str(entry.id)

    # Calculate totals for the entry
    total_estimated = sum(s.estimated or 0 for s in subentries)
    total_spent = sum(s.spent or 0 for s in subentries)

    # Get current shelf name (all subentries should be on the same shelf in this view)
    current_shelf_name = subentries[0].shelf_name if subentries else "Backlog"

    with ui.card().classes("w-full"):
        # Entry header with expand/collapse
        with ui.row().classes("w-full items-start justify-between mb-2"):
            with ui.column().classes("flex-1 gap-1"):
                emoji = get_emoji_for_type(entry.type)
                year = entry.release_date.year if entry.release_date else "N/A"
                rating_str = f"⭐ {entry.rating}" if entry.rating else "—"

                ui.label(f"{emoji} {entry.name}").classes("text-xl font-bold")
                with ui.row().classes("gap-4 text-sm flex-wrap"):
                    ui.label(f"Type: {entry.type.value}")
                    ui.label(f"Year: {year}")
                    ui.label(rating_str)
                    ui.label(f"Total Est: {format_minutes(total_estimated)}")
                    ui.label(
                        f"Total Spent: {format_minutes(total_spent) if total_spent else '—'}"
                    )
                    ui.label(f"Episodes: {len(subentries)}")
                if entry.notes:
                    ui.label(f"Notes: {entry.notes}").classes(
                        "text-sm text-gray-600 italic"
                    )

            # Move all subentries dropdown
            with ui.column().classes("ml-4 items-end gap-2"):
                ui.label("Move all:").classes("text-xs text-gray-600")

                entry_id_captured = str(entry.id)
                current_shelf_captured = current_shelf_name

                def make_move_all_callback(eid: str, current_shelf: str, ui_ref: dict):
                    async def on_move_all(e: ValueChangeEventArguments):
                        await update_all_subentries_shelf(eid, current_shelf, e.value, ui_ref)

                    return on_move_all

                ui.select(
                    options=get_all_shelves(),
                    value=current_shelf_name,
                    on_change=make_move_all_callback(
                        entry_id_captured, current_shelf_captured, shelves_ui
                    ),
                ).props("dense outlined")

        # Subentries list with expansion
        with ui.expansion("Episodes", icon="list").classes("w-full"):
            with ui.column().classes("w-full gap-1"):
                for subentry in sorted(subentries, key=lambda s: s.name or ""):
                    create_subentry_row(entry, subentry, shelves_ui)


def create_subentry_row(entry: Entry, subentry: SubEntry, shelves_ui: dict) -> None:
    """Create a row for a subentry within a grouped entry."""
    # Create container for the subentry
    with ui.row().classes("w-full items-center justify-between p-2 hover:bg-gray-100 rounded"):
        # Subentry info
        with ui.row().classes("flex-1 gap-4 items-center flex-wrap"):
            subentry_name = subentry.name or entry.name
            estimated_formatted = (
                format_minutes(subentry.estimated) if subentry.estimated else "N/A"
            )
            spent_formatted = format_minutes(subentry.spent) if subentry.spent else "—"
            status_str = "✓" if subentry.is_finished else "○"

            # Format release date
            release_date_str = "—"
            if subentry.release_date:
                release_date_str = subentry.release_date.strftime("%Y-%m-%d")

            ui.label(f"{status_str} {subentry_name}").classes("text-base font-medium")
            ui.label(f"Released: {release_date_str}").classes("text-sm")
            ui.label(f"Est: {estimated_formatted}").classes("text-sm")
            ui.label(f"Spent: {spent_formatted}").classes("text-sm")

        # Shelf selector dropdown
        with ui.column().classes("ml-4"):
            current_shelf_name = subentry.shelf_name
            entry_id_captured = str(entry.id)
            subentry_name_captured = subentry.name

            # Define callback that captures entry_id and subentry_name separately
            def make_callback(eid: str, sename: str, ui_ref: dict):
                async def on_shelf_selected(e: ValueChangeEventArguments):
                    await update_subentry_shelf(eid, sename, e.value, ui_ref)

                return on_shelf_selected

            ui.select(
                options=get_all_shelves(),
                value=current_shelf_name,
                on_change=make_callback(
                    entry_id_captured, subentry_name_captured, shelves_ui
                ),
            ).props("dense outlined").classes("min-w-[120px]")


async def setup_ui():
    """Setup the main UI with all subentries grouped by shelf."""
    global _shelves_ui_ref
    subentries_by_shelf = await load_subentries()

    with ui.column().classes("w-full max-w-6xl mx-auto p-4"):
        ui.label("📚 All Entries").classes("text-3xl font-bold mb-6")

        if not subentries_by_shelf:
            ui.label("No entries found.").classes("text-lg text-gray-500")
        else:
            # Sort shelves by weight using the shelf objects
            global _shelves_by_name
            shelf_names = list(subentries_by_shelf.keys())
            sorted_shelves = sorted(
                shelf_names,
                key=lambda x: _shelves_by_name[x].weight
                if x in _shelves_by_name
                else 999999,
            )

            # Create containers for each shelf that we can update
            shelves_ui = {}
            _shelves_ui_ref = shelves_ui  # Store globally for drag-drop handler
            for shelf in sorted_shelves:
                shelf_subentries = subentries_by_shelf[shelf]
                container = ui.column().classes("w-full gap-2")
                shelves_ui[shelf] = container
                build_shelf_content(shelf, shelf_subentries, shelves_ui, container)


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
