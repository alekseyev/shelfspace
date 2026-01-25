from datetime import date, timedelta
from enum import Enum
from nicegui import app, ui
from nicegui.events import ValueChangeEventArguments

from shelfspace.app_ctx import AppCtx
from shelfspace.models import Entry, Shelf, SubEntry, MediaType
from shelfspace.utils import format_minutes


class ViewMode(str, Enum):
    FINISHED = "Finished"
    ACTIVE = "Active"
    PLANNING = "Planning"


# Global reference to shelves_ui for drag-drop handling
_shelves_ui_ref: dict = {}

# Global shelf objects cache
_shelves_by_name: dict[str, Shelf] = {}
_shelves_by_id: dict = {}

# Global filter state
_current_filter: str = "All"

# Global search state
_current_search: str = ""

# Global view mode state
_current_view_mode: ViewMode = ViewMode.ACTIVE


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


def get_filter_categories():
    """Get filter categories with their corresponding MediaTypes."""
    return {
        "All": None,
        "Projects": [MediaType.PROJECTS],
        "Course": [MediaType.COURSE],
        "Movie": [MediaType.MOVIE],
        "Series": [MediaType.SERIES],
        "Books": [MediaType.BOOK, MediaType.BOOK_ED, MediaType.BOOK_COM],
        "Games": [MediaType.GAME, MediaType.GAME_VR, MediaType.GAME_MOBILE],
        "Other": [MediaType.ART, MediaType.VID],
    }


def matches_search(entry: Entry, subentry: SubEntry, search_query: str) -> bool:
    """Check if entry or subentry matches the search query."""
    if not search_query:
        return True

    query = search_query.lower()
    # Search in entry name
    if query in entry.name.lower():
        return True
    # Search in subentry name
    if subentry.name and query in subentry.name.lower():
        return True
    # Search in notes
    if entry.notes and query in entry.notes.lower():
        return True
    return False


def matches_filter(entry: Entry, filter_category: str) -> bool:
    """Check if entry matches the current filter."""
    filter_map = get_filter_categories()
    media_types = filter_map.get(filter_category)

    if media_types is None:  # "All" filter
        return True

    return entry.type in media_types


def get_shelf_time_display(
    shelf_obj: Shelf | None, estimated: int | None, spent: int | None
) -> str:
    """
    Get time display string based on shelf type.
    - Past shelves: only spent (⏱️)
    - Future shelves (start_date > today) or no-date shelves: only estimated (⏳)
    - Current shelves: spent/estimated together (⏱️)
    """
    today = date.today()

    # Determine shelf type
    is_past = False
    is_future = False

    if shelf_obj:
        if shelf_obj.end_date and shelf_obj.end_date < today:
            is_past = True
        elif shelf_obj.start_date and shelf_obj.start_date > today:
            is_future = True
        elif not shelf_obj.start_date and not shelf_obj.end_date:
            # No-date shelves (Backlog, Icebox) - treat as future (show estimated)
            is_future = True

    spent_str = format_minutes(spent) if spent else "0m"
    est_str = format_minutes(estimated) if estimated else "?"

    if is_past:
        return f"⏱️ {spent_str}"
    elif is_future:
        return f"⏳ {est_str}"
    else:
        # Current shelf - show both
        return f"⏱️ {spent_str}/{est_str}"


def format_release_date(release_date: date | None) -> str | None:
    """
    Format release date for display with 📅 emoji.
    - None: return None (don't display)
    - Future or within 2 weeks: show full date
    - Otherwise: show just year
    """
    if not release_date:
        return None

    today = date.today()
    two_weeks_ago = today - timedelta(days=14)

    if release_date > today or release_date >= two_weeks_ago:
        # Future or within last 2 weeks - show full date
        return f"📅 {release_date.strftime('%d %b %Y')}"
    else:
        # Past - show just year
        return f"📅 {release_date.year}"


def get_media_type_order(media_type: MediaType) -> int:
    """Get the sort order for a media type based on MediaType enum definition."""
    order = [
        MediaType.PROJECTS,
        MediaType.COURSE,
        MediaType.ART,
        MediaType.VID,
        MediaType.BOOK_ED,
        MediaType.BOOK,
        MediaType.BOOK_COM,
        MediaType.MOVIE,
        MediaType.SERIES,
        MediaType.GAME_VR,
        MediaType.GAME_MOBILE,
        MediaType.GAME,
    ]
    try:
        return order.index(media_type)
    except ValueError:
        return 999  # Unknown types go to the end


def is_dated_shelf(shelf: Shelf) -> bool:
    """Check if a shelf has dates (not Backlog/Icebox/Upcoming)."""
    return shelf.start_date is not None and shelf.end_date is not None


def filter_shelves_by_view_mode(
    shelves: list[Shelf], view_mode: ViewMode
) -> list[Shelf]:
    """Filter shelves based on view mode."""
    if view_mode == ViewMode.FINISHED:
        # Show only finished shelves
        return [s for s in shelves if s.is_finished]
    elif view_mode == ViewMode.ACTIVE:
        # Show non-finished dated shelves + Backlog (not Icebox)
        return [
            s
            for s in shelves
            if not s.is_finished and (is_dated_shelf(s) or s.name == "Backlog")
        ]
    elif view_mode == ViewMode.PLANNING:
        # Show only Backlog + Icebox (non-finished)
        return [
            s for s in shelves if not s.is_finished and s.name in ["Backlog", "Icebox"]
        ]
    return shelves


async def load_shelves(ignore_view_mode: bool = False) -> None:
    """Load shelves into global cache based on current view mode."""
    global _shelves_by_name, _shelves_by_id, _current_view_mode
    await AppCtx.ensure_initialized()

    # Load ALL shelves (both finished and not finished)
    all_shelves = await Shelf.find().to_list()

    # Build shelves_by_id dict for shelf_name property lookup
    _shelves_by_id = {shelf.id: shelf for shelf in all_shelves}
    Shelf._shelves_dict = _shelves_by_id

    # Filter shelves based on view mode (unless searching)
    if ignore_view_mode:
        filtered_shelves = all_shelves
    else:
        filtered_shelves = filter_shelves_by_view_mode(all_shelves, _current_view_mode)

    # Build name -> shelf mapping
    _shelves_by_name = {shelf.name: shelf for shelf in filtered_shelves}


async def load_subentries() -> dict[str, list[tuple[Entry, SubEntry]]]:
    """
    Load all subentries from the database grouped by shelf.

    Returns a dict mapping shelf names to lists of (entry, subentry) tuples.
    """
    global _current_view_mode, _current_search
    # When searching, load all shelves regardless of view mode
    await load_shelves(ignore_view_mode=bool(_current_search))
    entries = await Entry.find().to_list()

    # Group subentries by shelf name (resolved via shelf_id)
    subentries_by_shelf: dict[str, list[tuple[Entry, SubEntry]]] = {}
    for entry in entries:
        for subentry in entry.subentries:
            shelf_name = subentry.shelf_name
            if shelf_name not in _shelves_by_name:
                continue
            if shelf_name not in subentries_by_shelf:
                subentries_by_shelf[shelf_name] = []
            subentries_by_shelf[shelf_name].append((entry, subentry))

    # Ensure shelves visible in current view mode are present (even if empty)
    for shelf_name in _shelves_by_name:
        if shelf_name not in subentries_by_shelf:
            subentries_by_shelf[shelf_name] = []

    return subentries_by_shelf


def get_all_shelves() -> list[str]:
    """Get all possible shelf options sorted by weight."""
    global _shelves_by_name
    shelves = list(_shelves_by_name.values())
    # Sort by weight (lower weight comes first)
    shelves.sort(key=lambda s: s.weight)
    return [shelf.name for shelf in shelves]


def get_next_shelf(current_shelf: Shelf) -> Shelf | None:
    """Get the next shelf after the current one (by weight)."""
    global _shelves_by_name
    sorted_shelves = sorted(_shelves_by_name.values(), key=lambda s: s.weight)

    for i, shelf in enumerate(sorted_shelves):
        if shelf.id == current_shelf.id and i < len(sorted_shelves) - 1:
            return sorted_shelves[i + 1]

    return None


def can_finish_shelf(shelf: Shelf, subentries: list[tuple[Entry, SubEntry]]) -> bool:
    """
    Check if a shelf can be finished.

    A shelf can be finished if:
    - The shelf has already started (start_date <= today), AND
    - Today is on or after the last day of the shelf (based on end_date), OR
    - All subtasks in the shelf are finished
    """
    today = date.today()

    # Don't allow finishing shelves that haven't started yet
    if shelf.start_date and shelf.start_date > today:
        return False

    # Check if all subtasks are finished
    all_finished = all(subentry.is_finished for _, subentry in subentries)
    if all_finished:
        return True

    # Check if we're on or after the last day
    if shelf.end_date:
        return today >= shelf.end_date

    # Shelves without end_date (like Backlog, Icebox) can only be finished if all tasks are done
    return False


async def finish_shelf_dialog(shelf: Shelf, shelves_ui: dict) -> None:
    """Show confirmation dialog before finishing a shelf."""
    # Count unfinished subentries
    entries = await Entry.find().to_list()
    unfinished_count = 0
    for entry in entries:
        for subentry in entry.subentries:
            if subentry.shelf_id == shelf.id and not subentry.is_finished:
                unfinished_count += 1

    next_shelf = get_next_shelf(shelf)
    next_shelf_name = next_shelf.name if next_shelf else "Unknown"

    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Finish Shelf: {shelf.name}").classes("text-xl font-bold mb-4")

        if unfinished_count > 0:
            ui.label(
                f"This shelf has {unfinished_count} unfinished task(s) that will be moved to {next_shelf_name}."
            ).classes("text-sm mb-2")
            ui.label(
                "Tasks with time spent will be marked as finished and recreated in the next shelf with remaining time."
            ).classes("text-xs text-gray-600 mb-2")
        else:
            ui.label("All tasks in this shelf are finished.").classes("text-sm mb-2")

        ui.label("Are you sure you want to finish this shelf?").classes(
            "text-sm font-semibold mb-4"
        )

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def confirm_finish():
                dialog.close()
                await finish_shelf(shelf, shelves_ui)

            ui.button("Finish Shelf", on_click=confirm_finish).props("color=positive")

    dialog.open()


async def finish_shelf(shelf: Shelf, shelves_ui: dict) -> None:
    """
    Finish a shelf by:
    1. Setting is_finished to True
    2. For each unfinished SubEntry:
       - If time spent: finish it and create new SubEntry in next shelf with remaining time
       - If no time spent: move SubEntry to next shelf
    """
    # Get the next shelf
    next_shelf = get_next_shelf(shelf)
    if not next_shelf:
        ui.notify("No next shelf available to move unfinished tasks", type="warning")
        return

    # Get all entries with subentries in this shelf
    entries = await Entry.find().to_list()
    modified_entries = []

    for entry in entries:
        modified = False
        for subentry in entry.subentries:
            # Only process subentries in this shelf that aren't finished
            if subentry.shelf_id == shelf.id and not subentry.is_finished:
                if subentry.spent and subentry.spent > 0:
                    # There's time spent - finish this subentry and create new one in next shelf
                    # Save original estimated before modifying
                    original_estimated = subentry.estimated

                    # Finish current subentry
                    subentry.is_finished = True
                    subentry.estimated = subentry.spent

                    # Calculate remaining time (only if there was an original estimate)
                    if original_estimated and original_estimated > subentry.spent:
                        remaining_time = original_estimated - subentry.spent

                        # Create new subentry in next shelf with remaining time
                        new_subentry = SubEntry(
                            shelf_id=next_shelf.id,
                            name=subentry.name,
                            estimated=remaining_time,
                            spent=0,
                            is_finished=False,
                            release_date=subentry.release_date,
                            metadata=subentry.metadata.copy()
                            if subentry.metadata
                            else {},
                        )
                        entry.subentries.append(new_subentry)

                    modified = True
                else:
                    # No time spent - just move to next shelf
                    subentry.shelf_id = next_shelf.id
                    modified = True

        if modified:
            modified_entries.append(entry)

    # Save all modified entries
    for entry in modified_entries:
        await entry.save()

    # Mark shelf as finished
    shelf.is_finished = True
    await shelf.save()

    ui.notify(
        f"Shelf '{shelf.name}' finished. Moved {len(modified_entries)} entries to {next_shelf.name}",
        type="positive",
    )

    # Reload shelves and refresh UI
    await load_shelves()
    await refresh_all_shelves(shelves_ui)


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
    """Update all non-finished subentries of an entry on a specific shelf to a new shelf."""
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

    # Update all non-finished subentries on the current shelf to the new shelf
    moved_count = 0
    for subentry in entry_obj.subentries:
        if subentry.shelf_id == current_shelf.id and not subentry.is_finished:
            subentry.shelf_id = new_shelf.id
            moved_count += 1

    await entry_obj.save()

    # Notify user about the move
    if moved_count > 0:
        ui.notify(
            f"Moved {moved_count} episode(s) to {new_shelf_name}", type="positive"
        )

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
    global _current_filter, _current_search

    # Apply filter and search
    filtered_subentries = [
        (entry, sub)
        for entry, sub in subentries
        if matches_filter(entry, _current_filter)
        and matches_search(entry, sub, _current_search)
    ]

    # Don't show empty shelves when searching
    if _current_search and not filtered_subentries:
        return

    subentry_count = len(filtered_subentries)

    # Calculate totals for the shelf
    total_estimated = sum(sub.estimated or 0 for _, sub in filtered_subentries)
    total_spent = sum(sub.spent or 0 for _, sub in filtered_subentries)

    # Get shelf object for time display formatting
    shelf_obj = _shelves_by_name.get(shelf)
    time_display = get_shelf_time_display(shelf_obj, total_estimated, total_spent)

    with container:
        # Shelf header with totals (compact)
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            ui.label(f"📚 {shelf}").classes("text-base font-semibold")
            ui.label(f"({subentry_count})").classes("text-xs text-gray-500")
            ui.label(time_display).classes("text-xs text-gray-600")

            # Add finish shelf button if shelf can be finished (not when searching)
            if not _current_search:
                if shelf_obj and not shelf_obj.is_finished:
                    if can_finish_shelf(shelf_obj, filtered_subentries):

                        async def finish_shelf_handler(s=shelf_obj, ui_ref=shelves_ui):
                            await finish_shelf_dialog(s, ui_ref)

                        ui.button(
                            "Finish",
                            icon="check_circle",
                            on_click=finish_shelf_handler,
                        ).props("dense size=xs color=positive").classes("ml-auto")

        # Create the container for this shelf (compact)
        shelf_container = ui.column().classes(
            "w-full p-1 border border-gray-300 rounded bg-gray-50 gap-0"
        )

        with shelf_container:
            if not filtered_subentries:
                ui.label("No entries").classes(
                    "text-center text-gray-400 py-1 italic w-full text-xs"
                )
            else:
                # Group subentries by entry
                entries_map: dict[str, list[SubEntry]] = {}
                entry_objects: dict[str, Entry] = {}
                for entry, subentry in filtered_subentries:
                    entry_id = str(entry.id)
                    if entry_id not in entries_map:
                        entries_map[entry_id] = []
                        entry_objects[entry_id] = entry
                    entries_map[entry_id].append(subentry)

                # Sort entries by media type order first, then by release date, then by name
                sorted_entry_ids = sorted(
                    entries_map.keys(),
                    key=lambda eid: (
                        get_media_type_order(entry_objects[eid].type),
                        entry_objects[eid].release_date or date.max,
                        entry_objects[eid].name,
                    ),
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
    global _current_view_mode, _shelves_by_name
    is_finished = subentry.is_finished
    card_classes = "w-full p-1 min-h-[40px]"
    # Only grey out finished entries if NOT in Finished view mode
    if is_finished and _current_view_mode != ViewMode.FINISHED:
        card_classes += " opacity-60 bg-gray-100"

    # Get shelf object for time display formatting
    shelf_obj = _shelves_by_name.get(subentry.shelf_name)

    with ui.card().classes(card_classes):
        with ui.row().classes("w-full items-center justify-between gap-1 min-h-[32px]"):
            # Main content - single row with title and info
            emoji = get_emoji_for_type(entry.type)
            subentry_name = subentry.name or entry.name

            # Format time display based on shelf type
            time_display = get_shelf_time_display(
                shelf_obj, subentry.estimated, subentry.spent
            )
            # Format release date
            date_display = format_release_date(entry.release_date)
            rating_str = f"⭐ {entry.rating}" if entry.rating else ""

            with ui.row().classes("flex-1 items-center gap-2 flex-wrap"):
                # Spacer to align with series expand button
                ui.element("div").classes("w-6")

                # Edit button (aligned with series edit button)
                ui.button(
                    icon="edit",
                    on_click=lambda e=entry, s=subentry: edit_entry_dialog(
                        e, s, shelves_ui
                    ),
                ).props("flat dense round size=xs").classes("text-gray-400")

                entry_label = ui.label(f"{emoji} {subentry_name}").classes(
                    "text-sm font-semibold"
                )
                # Add double-click handler for editing
                entry_label.on(
                    "dblclick",
                    lambda e=entry, s=subentry: edit_entry_dialog(e, s, shelves_ui),
                )

                # Info inline with title
                ui.label(time_display).classes("text-xs text-gray-600")
                if date_display:
                    ui.label(date_display).classes("text-xs text-gray-600")
                if rating_str:
                    ui.label(rating_str).classes("text-xs")
                if entry.notes:
                    ui.label(f"📝 {entry.notes}").classes(
                        "text-xs text-gray-500 italic"
                    )

            # Action buttons
            with ui.row().classes("items-center gap-0"):
                # Add time button for non-movie/non-series entries that aren't finished
                if not is_finished and entry.type not in [
                    MediaType.MOVIE,
                    MediaType.SERIES,
                ]:
                    ui.button(
                        icon="add",
                        on_click=lambda e=entry, s=subentry: add_time_dialog(
                            e, s, shelves_ui
                        ),
                    ).props("flat dense round size=xs").classes("text-purple-500")
                # Add finish button for non-movie/non-series entries that aren't finished
                if not is_finished and entry.type not in [
                    MediaType.MOVIE,
                    MediaType.SERIES,
                ]:
                    ui.button(
                        icon="check_circle",
                        on_click=lambda e=entry, s=subentry: finish_entry_dialog(
                            e, s, shelves_ui
                        ),
                    ).props("flat dense round size=xs").classes("text-green-500")
                # Add copy to next shelf button for non-movie/non-series entries that aren't finished
                if not is_finished and entry.type not in [
                    MediaType.MOVIE,
                    MediaType.SERIES,
                ]:
                    ui.button(
                        icon="arrow_forward",
                        on_click=lambda e=entry, s=subentry: copy_to_next_shelf(
                            e, s, shelves_ui
                        ),
                    ).props("flat dense round size=xs").classes("text-blue-500")

            # Shelf selector and delete button (only show shelf selector if not finished)
            with ui.row().classes("ml-2 items-center gap-1"):
                if not is_finished:
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
                    ).props("dense outlined").classes("text-xs")

                    # Delete button on the right of shelf selector
                    ui.button(
                        icon="close",
                        on_click=lambda e=entry, s=subentry: delete_entry_dialog(
                            e, s, shelves_ui
                        ),
                    ).props("flat dense round size=xs").classes("text-red-500")


def create_grouped_entry_card(
    entry: Entry, subentries: list[SubEntry], shelves_ui: dict
) -> None:
    """Create a card for an entry with multiple subentries grouped together."""
    global _current_view_mode, _shelves_by_name
    # Calculate totals for the entry
    total_estimated = sum(s.estimated or 0 for s in subentries)
    total_spent = sum(s.spent or 0 for s in subentries)

    # Check if all subentries are finished
    is_finished = all(s.is_finished for s in subentries)

    # Get current shelf name (all subentries should be on the same shelf in this view)
    current_shelf_name = subentries[0].shelf_name if subentries else "Backlog"
    shelf_obj = _shelves_by_name.get(current_shelf_name)

    # Count episodes on current shelf vs total
    episodes_on_shelf = len(subentries)
    total_episodes = len(entry.subentries)
    finished_on_shelf = sum(1 for s in subentries if s.is_finished)

    # Format: "2/7" or "1/2 (7 total)" if split
    episode_count_str = f"{finished_on_shelf}/{episodes_on_shelf}"
    if episodes_on_shelf != total_episodes:
        episode_count_str += f" ({total_episodes} total)"

    card_classes = "w-full p-1 min-h-[40px]"
    # Only grey out finished entries if NOT in Finished view mode
    if is_finished and _current_view_mode != ViewMode.FINISHED:
        card_classes += " opacity-60 bg-gray-100"

    # Format time display based on shelf type
    time_display = get_shelf_time_display(shelf_obj, total_estimated, total_spent)
    # Format release date
    date_display = format_release_date(entry.release_date)
    rating_str = f"⭐ {entry.rating}" if entry.rating else ""
    emoji = get_emoji_for_type(entry.type)

    with ui.card().classes(card_classes):
        # Entry header with expand/collapse
        with ui.row().classes("w-full items-center justify-between gap-1 min-h-[32px]"):
            # Will hold reference to episodes container and button for toggle
            episodes_container = None
            expand_btn = None

            # Header row with expand arrow and info inline
            with ui.row().classes("flex-1 items-center gap-2 flex-wrap"):
                expand_btn = (
                    ui.button(
                        icon="chevron_right",
                        on_click=lambda: toggle_episodes(),
                    )
                    .props("flat dense round size=xs")
                    .classes("text-gray-500")
                )

                # Edit button right after expand (aligned with single entry edit button)
                ui.button(
                    icon="edit",
                    on_click=lambda e=entry: edit_entry_dialog(e, None, shelves_ui),
                ).props("flat dense round size=xs").classes("text-gray-400")

                ui.label(f"{emoji} {entry.name}").classes("text-sm font-bold")

                # Info inline
                ui.label(time_display).classes("text-xs text-gray-600")
                if date_display:
                    ui.label(date_display).classes("text-xs text-gray-600")
                if rating_str:
                    ui.label(rating_str).classes("text-xs")
                ui.label(f"📺 {episode_count_str}").classes("text-xs text-gray-600")
                if entry.notes:
                    ui.label(f"📝 {entry.notes}").classes(
                        "text-xs text-gray-500 italic"
                    )

            def toggle_episodes():
                episodes_container.visible = not episodes_container.visible
                expand_btn.props(
                    f"icon={'expand_more' if episodes_container.visible else 'chevron_right'}"
                )

            # Shelf selector and delete button for series
            with ui.row().classes("ml-auto items-center gap-1"):
                # Move all subentries dropdown (only show if not finished)
                if not is_finished:
                    entry_id_captured = str(entry.id)
                    current_shelf_captured = current_shelf_name

                    def make_move_all_callback(
                        eid: str, current_shelf: str, ui_ref: dict
                    ):
                        async def on_move_all(e: ValueChangeEventArguments):
                            await update_all_subentries_shelf(
                                eid, current_shelf, e.value, ui_ref
                            )

                        return on_move_all

                    ui.select(
                        options=get_all_shelves(),
                        value=current_shelf_name,
                        on_change=make_move_all_callback(
                            entry_id_captured, current_shelf_captured, shelves_ui
                        ),
                    ).props("dense outlined").classes("text-xs min-w-[80px]")

                    # Delete button on the right of shelf selector
                    ui.button(
                        icon="close",
                        on_click=lambda e=entry: delete_entry_dialog(
                            e, None, shelves_ui
                        ),
                    ).props("flat dense round size=xs").classes("text-red-500")

        # Subentries list (collapsible)
        episodes_container = ui.column().classes("w-full gap-0 mt-0.5 ml-6")
        episodes_container.visible = False
        with episodes_container:
            for subentry in sorted(subentries, key=lambda s: s.name or ""):
                create_subentry_row(entry, subentry, shelves_ui)


def create_subentry_row(entry: Entry, subentry: SubEntry, shelves_ui: dict) -> None:
    """Create a row for a subentry within a grouped entry."""
    global _shelves_by_name

    # Get shelf object for time display formatting
    shelf_obj = _shelves_by_name.get(subentry.shelf_name)

    # Create container for the subentry
    with ui.row().classes(
        "w-full items-center justify-between px-1 hover:bg-gray-100 rounded"
    ):
        # Subentry info
        with ui.row().classes("flex-1 gap-2 items-center flex-wrap"):
            subentry_name = subentry.name or entry.name
            status_str = "✓" if subentry.is_finished else "○"

            # Format time display based on shelf type
            time_display = get_shelf_time_display(
                shelf_obj, subentry.estimated, subentry.spent
            )

            # Format release date for subentry
            release_date_str = format_release_date(subentry.release_date)

            ui.label(f"{status_str} {subentry_name}").classes("text-xs")
            ui.label(time_display).classes("text-xs text-gray-500")
            if release_date_str:
                ui.label(release_date_str).classes("text-xs text-gray-500")

        # Shelf selector dropdown (only show if not finished)
        if not subentry.is_finished:
            with ui.column().classes("ml-1"):
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
                ).props("dense outlined").classes("min-w-[90px] text-xs")


async def add_entry_dialog(shelves_ui: dict) -> None:
    """Show dialog to add a new entry."""
    # Fetch existing entries for autocomplete
    existing_entries = await Entry.find().to_list()
    entry_options = {
        str(e.id): f"{get_emoji_for_type(e.type.value)} {e.name}"
        for e in existing_entries
    }
    entries_by_id = {str(e.id): e for e in existing_entries}

    # Track whether we're adding to existing entry or creating new
    selected_entry: Entry | None = None

    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label("Add New Entry").classes("text-xl font-bold mb-4")

        # Form inputs - use combo-box for name with autocomplete
        # key_generator creates keys for newly typed values (prefixed to avoid collision)
        name_select = (
            ui.select(
                options=entry_options,
                label="Entry Name",
                with_input=True,
                new_value_mode="add",
                key_generator=lambda v: f"new:{v}",
            )
            .props("outlined dense use-input input-debounce=200")
            .classes("w-full")
        )

        # Mode indicator label
        mode_label = ui.label("Type a new name or select existing entry").classes(
            "text-sm text-gray-500"
        )

        type_select = (
            ui.select(
                options=[t.value for t in MediaType],
                label="Type",
                value=MediaType.MOVIE.value,
            )
            .props("outlined dense")
            .classes("w-full")
        )

        notes_input = ui.textarea("Notes").props("outlined dense").classes("w-full")

        with ui.row().classes("w-full gap-2"):
            estimated_hours = (
                ui.number("Estimated Hours", value=None, format="%.1f")
                .props("outlined dense")
                .classes("flex-1")
            )
            shelf_options = get_all_shelves()
            shelf_value = shelf_options[-1] if shelf_options else None
            shelf_select = (
                ui.select(
                    options=shelf_options,
                    label="Shelf",
                    value=shelf_value,
                )
                .props("outlined dense")
                .classes("flex-1")
            )

        def on_name_change(e):
            nonlocal selected_entry
            value = e.value
            if value in entries_by_id:
                # Existing entry selected
                selected_entry = entries_by_id[value]
                type_select.set_value(selected_entry.type.value)
                type_select.disable()
                mode_label.set_text(f"Adding subentry to: {selected_entry.name}")
                mode_label.classes(
                    remove="text-gray-500", add="text-blue-600 font-medium"
                )
            else:
                # New name typed
                selected_entry = None
                type_select.enable()
                if value:
                    mode_label.set_text("Will create new entry")
                    mode_label.classes(
                        remove="text-blue-600 font-medium", add="text-gray-500"
                    )
                else:
                    mode_label.set_text("Type a new name or select existing entry")
                    mode_label.classes(
                        remove="text-blue-600 font-medium", add="text-gray-500"
                    )

        name_select.on_value_change(on_name_change)

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def save_entry():
                if not name_select.value:
                    ui.notify("Name is required", type="negative")
                    return

                # Get shelf object
                shelf_obj = _shelves_by_name.get(shelf_select.value)
                if not shelf_obj:
                    ui.notify("Invalid shelf selected", type="negative")
                    return

                new_subentry = SubEntry(
                    shelf_id=shelf_obj.id,
                    estimated=int(estimated_hours.value * 60)
                    if estimated_hours.value
                    else None,
                )

                if selected_entry:
                    # Add subentry to existing entry
                    selected_entry.subentries.append(new_subentry)
                    await selected_entry.save()
                    ui.notify(
                        f"Subentry added to '{selected_entry.name}'",
                        type="positive",
                    )
                else:
                    # Create new entry with one subentry
                    # Strip "new:" prefix from key_generator
                    entry_name = name_select.value
                    if entry_name.startswith("new:"):
                        entry_name = entry_name[4:]
                    entry = Entry(
                        type=MediaType(type_select.value),
                        name=entry_name,
                        notes=notes_input.value or "",
                        subentries=[new_subentry],
                    )
                    await entry.save()
                    ui.notify(
                        f"Entry '{entry.name}' created successfully", type="positive"
                    )

                dialog.close()

                # Refresh UI
                await refresh_all_shelves(shelves_ui)

            ui.button("Save", on_click=save_entry).props("color=primary")

    dialog.open()


async def add_shelf_dialog() -> None:
    """Show dialog to add a new shelf."""
    # Find the last dated shelf to calculate default start date
    all_shelves = await Shelf.find().to_list()
    dated_shelves = [s for s in all_shelves if s.end_date is not None]

    default_start = ""
    if dated_shelves:
        # Find the shelf with the latest end_date
        latest_shelf = max(dated_shelves, key=lambda s: s.end_date)
        default_start = (latest_shelf.end_date + timedelta(days=1)).isoformat()

    # Calculate default end date (start + 6 days)
    default_end = ""
    if default_start:
        default_end = (
            date.fromisoformat(default_start) + timedelta(days=6)
        ).isoformat()

    with ui.dialog() as dialog, ui.card().classes("p-4 min-w-[350px]"):
        ui.label("Add New Shelf").classes("text-xl font-bold mb-4")

        # Form inputs
        with ui.row().classes("w-full gap-2"):

            def on_start_date_change(e):
                """Update end date when start date changes."""
                if e.value:
                    try:
                        new_end = date.fromisoformat(e.value) + timedelta(days=6)
                        end_date_input.set_value(new_end.isoformat())
                    except ValueError:
                        pass

            start_date_input = (
                ui.input(
                    "Start Date", value=default_start, on_change=on_start_date_change
                )
                .props("outlined dense type=date")
                .classes("flex-1")
            )
            end_date_input = (
                ui.input("End Date", value=default_end)
                .props("outlined dense type=date")
                .classes("flex-1")
            )

        ui.label(
            "Shelf name will be auto-generated from dates (e.g., '15 January - 22 January 2025')"
        ).classes("text-xs text-gray-600 mt-1")

        description_input = (
            ui.textarea("Description (optional)")
            .props("outlined dense")
            .classes("w-full")
        )

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def save_new_shelf():
                if not start_date_input.value or not end_date_input.value:
                    ui.notify("Start and end dates are required", type="negative")
                    return

                # Parse dates
                try:
                    start = date.fromisoformat(start_date_input.value)
                    end = date.fromisoformat(end_date_input.value)
                except ValueError:
                    ui.notify("Invalid date format", type="negative")
                    return

                if end < start:
                    ui.notify("End date must be after start date", type="negative")
                    return

                # Create shelf (name and weight auto-generated on save)
                shelf = Shelf(
                    start_date=start,
                    end_date=end,
                    description=description_input.value or "",
                )

                await shelf.save()
                ui.notify(f"Shelf '{shelf.name}' created successfully", type="positive")
                dialog.close()

                # Rebuild UI to show new shelf
                ui.navigate.to("/")

            ui.button("Save", on_click=save_new_shelf).props("color=primary")

    dialog.open()


async def copy_to_next_shelf(
    entry: Entry, current_subentry: SubEntry, shelves_ui: dict
) -> None:
    """Copy the entry to the next shelf with 0 spent time."""
    global _shelves_by_name

    # Get current shelf
    current_shelf = None
    for shelf in _shelves_by_name.values():
        if shelf.id == current_subentry.shelf_id:
            current_shelf = shelf
            break

    if not current_shelf:
        ui.notify("Could not find current shelf", type="negative")
        return

    # Get all shelves sorted by weight
    sorted_shelves = sorted(_shelves_by_name.values(), key=lambda s: s.weight)

    # Find next shelf
    next_shelf = None
    for i, shelf in enumerate(sorted_shelves):
        if shelf.id == current_shelf.id and i < len(sorted_shelves) - 1:
            next_shelf = sorted_shelves[i + 1]
            break

    if not next_shelf:
        ui.notify("No next shelf available", type="warning")
        return

    # Check if entry already has a subentry in the next shelf
    for subentry in entry.subentries:
        if subentry.shelf_id == next_shelf.id:
            ui.notify(f"Entry already exists in {next_shelf.name}", type="warning")
            return

    # Create new subentry
    new_subentry = SubEntry(
        shelf_id=next_shelf.id,
        name=current_subentry.name,
        estimated=current_subentry.estimated,
        spent=0,
        is_finished=False,
    )

    entry.subentries.append(new_subentry)
    await entry.save()

    ui.notify(f"Entry copied to {next_shelf.name}", type="positive")

    # Refresh UI
    await refresh_all_shelves(shelves_ui)


async def add_time_dialog(entry: Entry, subentry: SubEntry, shelves_ui: dict) -> None:
    """Show dialog to add time to spent time."""
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Add Time: {entry.name}").classes("text-xl font-bold mb-4")

        # Hours to add input
        add_hours_input = (
            ui.number("Hours to Add", value=None, format="%.1f")
            .props("outlined dense")
            .classes("w-full")
        )

        current_spent = subentry.spent / 60 if subentry.spent else 0
        ui.label(f"Current spent time: {current_spent:.1f}h").classes(
            "text-xs text-gray-600 mt-2"
        )

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def add_time():
                if add_hours_input.value is None or add_hours_input.value <= 0:
                    ui.notify(
                        "Please enter a positive number of hours", type="negative"
                    )
                    return

                # Add time to spent
                add_minutes = int(add_hours_input.value * 60)
                subentry.spent = (subentry.spent or 0) + add_minutes

                await entry.save()
                new_spent = subentry.spent / 60
                ui.notify(
                    f"Added {add_hours_input.value:.1f}h (total: {new_spent:.1f}h)",
                    type="positive",
                )
                dialog.close()

                # Refresh UI
                await refresh_all_shelves(shelves_ui)

            ui.button("Add", on_click=add_time).props("color=primary")

    dialog.open()


async def finish_entry_dialog(
    entry: Entry, subentry: SubEntry, shelves_ui: dict
) -> None:
    """Show dialog to mark entry as finished with final spent time."""
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Finish: {entry.name}").classes("text-xl font-bold mb-4")

        # Spent time input
        spent_hours_input = (
            ui.number(
                "Spent Hours",
                value=subentry.spent / 60
                if subentry.spent
                else (subentry.estimated / 60 if subentry.estimated else None),
                format="%.1f",
            )
            .props("outlined dense")
            .classes("w-full")
        )

        ui.label(
            "This will set both estimated and spent time to the value above and mark the entry as finished."
        ).classes("text-xs text-gray-600 mt-2")

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def mark_finished():
                if spent_hours_input.value is None:
                    ui.notify("Spent hours is required", type="negative")
                    return

                # Set both estimated and spent to the same value
                spent_minutes = int(spent_hours_input.value * 60)
                subentry.estimated = spent_minutes
                subentry.spent = spent_minutes
                subentry.is_finished = True

                await entry.save()
                ui.notify(f"Entry '{entry.name}' marked as finished", type="positive")
                dialog.close()

                # Refresh UI
                await refresh_all_shelves(shelves_ui)

            ui.button("Finish", on_click=mark_finished).props("color=positive")

    dialog.open()


async def delete_entry_or_subentry(
    entry: Entry, subentry: SubEntry | None, shelves_ui: dict
) -> None:
    """
    Delete logic:
    - If subentry has spent time: mark as finished (set estimated = spent time)
    - If entry has only 1 subentry with no time tracked and not finished: delete entire entry
    - If entry has multiple subentries: delete only that subentry
    """
    if subentry is None:
        # Deleting entire entry (from series grouped view)
        # Remove entry entirely
        await entry.delete()
        ui.notify(f"Entry '{entry.name}' deleted successfully", type="positive")
    else:
        # Deleting a specific subentry
        if subentry.spent and subentry.spent > 0:
            # Has spent time - mark as finished instead of deleting
            subentry.is_finished = True
            subentry.estimated = subentry.spent
            await entry.save()
            ui.notify(
                f"Entry '{entry.name}' marked as finished (has spent time)",
                type="positive",
            )
        elif len(entry.subentries) == 1:
            # Only one subentry with no time - delete entire entry
            await entry.delete()
            ui.notify(f"Entry '{entry.name}' deleted successfully", type="positive")
        else:
            # Multiple subentries - delete only this one
            entry.subentries.remove(subentry)
            await entry.save()
            ui.notify("Subentry deleted successfully", type="positive")

    # Refresh UI
    await refresh_all_shelves(shelves_ui)


async def delete_entry_dialog(
    entry: Entry, subentry: SubEntry | None, shelves_ui: dict
) -> None:
    """Show confirmation dialog before deleting an entry or subentry."""
    # Determine what will happen
    action_text = "delete this entry"
    reason_text = ""

    if subentry is not None:
        if subentry.spent and subentry.spent > 0:
            action_text = "mark this entry as finished"
            reason_text = f"(has {format_minutes(subentry.spent)} spent time)"
        elif len(entry.subentries) == 1:
            action_text = "delete this entire entry"
            reason_text = "(only subentry with no time tracked)"
        else:
            action_text = "delete this subentry"
            reason_text = f"({len(entry.subentries) - 1} other subentries will remain)"
    else:
        action_text = "delete this entry entirely"

    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Delete: {entry.name}").classes("text-xl font-bold mb-4")
        ui.label(f"Are you sure you want to {action_text}?").classes("text-sm mb-2")
        if reason_text:
            ui.label(reason_text).classes("text-xs text-gray-600 mb-4")

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def confirm_delete():
                dialog.close()
                await delete_entry_or_subentry(entry, subentry, shelves_ui)

            ui.button("Delete", on_click=confirm_delete).props("color=negative")

    dialog.open()


async def edit_entry_dialog(
    entry: Entry, subentry: SubEntry | None, shelves_ui: dict
) -> None:
    """Show dialog to edit an existing entry."""
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(f"Edit Entry: {entry.name}").classes("text-xl font-bold mb-4")

        # Form inputs
        name_input = (
            ui.input("Name", value=entry.name).props("outlined dense").classes("w-full")
        )
        type_select = (
            ui.select(
                options=[t.value for t in MediaType],
                label="Type",
                value=entry.type.value,
            )
            .props("outlined dense")
            .classes("w-full")
        )

        notes_input = (
            ui.textarea("Notes", value=entry.notes)
            .props("outlined dense")
            .classes("w-full")
        )

        # Links input (one per line)
        links_input = (
            ui.textarea("Links (one per line)", value="\n".join(entry.links))
            .props("outlined dense")
            .classes("w-full")
        )

        # Time fields - only show if NOT movie/series AND we have a subentry
        estimated_hours_input = None
        spent_hours_input = None
        if subentry and entry.type not in [MediaType.MOVIE, MediaType.SERIES]:
            with ui.row().classes("w-full gap-2"):
                estimated_hours_input = (
                    ui.number(
                        "Estimated Hours",
                        value=subentry.estimated / 60 if subentry.estimated else None,
                        format="%.1f",
                    )
                    .props("outlined dense")
                    .classes("flex-1")
                )
                spent_hours_input = (
                    ui.number(
                        "Spent Hours",
                        value=subentry.spent / 60 if subentry.spent else None,
                        format="%.1f",
                    )
                    .props("outlined dense")
                    .classes("flex-1")
                )

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            async def save_changes():
                if not name_input.value:
                    ui.notify("Name is required", type="negative")
                    return

                # Update entry
                entry.name = name_input.value
                entry.type = MediaType(type_select.value)
                entry.notes = notes_input.value or ""

                # Update links (split by newlines and filter empty)
                entry.links = [
                    link.strip()
                    for link in (links_input.value or "").split("\n")
                    if link.strip()
                ]

                # Update subentry time if applicable
                if subentry and estimated_hours_input is not None:
                    subentry.estimated = (
                        int(estimated_hours_input.value * 60)
                        if estimated_hours_input.value
                        else None
                    )
                if subentry and spent_hours_input is not None:
                    subentry.spent = (
                        int(spent_hours_input.value * 60)
                        if spent_hours_input.value
                        else 0
                    )

                await entry.save()
                ui.notify(f"Entry '{entry.name}' updated successfully", type="positive")
                dialog.close()

                # Refresh UI
                await refresh_all_shelves(shelves_ui)

            ui.button("Save", on_click=save_changes).props("color=primary")

    dialog.open()


async def refresh_all_shelves(shelves_ui: dict) -> None:
    """Refresh all shelf containers."""
    subentries_by_shelf = await load_subentries()

    for shelf_name, container_ref in shelves_ui.items():
        container_ref.clear()
        shelf_subentries = subentries_by_shelf.get(shelf_name, [])
        build_shelf_content(shelf_name, shelf_subentries, shelves_ui, container_ref)


async def setup_ui():
    """Setup the main UI with all subentries grouped by shelf."""
    global _shelves_ui_ref, _current_filter, _current_view_mode
    subentries_by_shelf = await load_subentries()

    with ui.column().classes("w-full max-w-6xl mx-auto p-3"):
        # Header with title and add buttons
        with ui.row().classes("w-full items-center justify-between mb-4"):
            ui.label("Shelfspace").classes("text-2xl font-bold")
            with ui.row().classes("gap-2"):
                ui.button(
                    "Add Shelf",
                    icon="date_range",
                    on_click=add_shelf_dialog,
                ).props("color=secondary outline")
                ui.button(
                    "Add Entry",
                    icon="add",
                    on_click=lambda: add_entry_dialog(_shelves_ui_ref),
                ).props("color=primary")

        # View mode selector (hidden when searching)
        if not _current_search:
            with ui.row().classes("w-full gap-2 mb-2 flex-wrap"):
                ui.label("View:").classes("text-sm font-medium")

                async def apply_view_mode(mode: ViewMode):
                    global _current_view_mode
                    _current_view_mode = mode

                    # Rebuild entire UI with new view mode
                    ui.navigate.to("/")

                with ui.row().classes("gap-2"):
                    for mode in ViewMode:
                        is_active = mode == _current_view_mode

                        ui.button(
                            mode.value,
                            on_click=lambda m=mode: apply_view_mode(m),
                        ).props(
                            f"{'unelevated' if is_active else 'outline'} dense size=sm"
                        ).classes(f"{'bg-secondary text-white' if is_active else ''}")

        # Filter bar (hidden when searching)
        if not _current_search:
            with ui.row().classes("w-full gap-2 mb-4 flex-wrap"):
                ui.label("Filter:").classes("text-sm font-medium")

                filter_categories = list(get_filter_categories().keys())

                async def apply_filter(category: str):
                    global _current_filter
                    _current_filter = category

                    # Refresh all shelves with new filter
                    await refresh_all_shelves(_shelves_ui_ref)

                with ui.row().classes("gap-2"):
                    for category in filter_categories:
                        is_active = category == _current_filter

                        ui.button(
                            category,
                            on_click=lambda cat=category: apply_filter(cat),
                        ).props(
                            f"{'unelevated' if is_active else 'outline'} dense size=sm"
                        ).classes(f"{'bg-primary text-white' if is_active else ''}")

        # Search bar
        with ui.row().classes("w-full gap-2 mb-4 items-center"):
            ui.label("Search:").classes("text-sm font-medium")

            async def apply_search(e):
                global _current_search
                was_searching = bool(_current_search)
                _current_search = e.value or ""
                is_searching = bool(_current_search)

                # Only rebuild page when clearing search (to show view mode/filter again)
                # When starting to search, just refresh shelves to avoid losing focus
                if was_searching and not is_searching:
                    ui.navigate.to("/")
                else:
                    await refresh_all_shelves(_shelves_ui_ref)

            search_input = (
                ui.input(
                    placeholder="Search entries... (/ or . to focus)",
                    value=_current_search,
                    on_change=apply_search,
                )
                .props("outlined dense clearable")
                .classes("flex-1 max-w-md")
            )

            # Keyboard shortcuts to focus search
            ui.keyboard(
                on_key=lambda e: search_input.run_method("focus")
                if e.key in ["/", "."] and not e.action.repeat
                else None,
                ignore=["input", "textarea"],
            )

        if not subentries_by_shelf:
            ui.label("No entries found.").classes("text-base text-gray-500")
        else:
            # Sort shelves by weight using the shelf objects
            global _shelves_by_name
            shelf_names = list(subentries_by_shelf.keys())
            sorted_shelves = sorted(
                shelf_names,
                key=lambda x: _shelves_by_name[x].weight
                if x in _shelves_by_name
                else 999999,
                # Reverse for finished mode (newest on top = highest weight first)
                reverse=(_current_view_mode == ViewMode.FINISHED),
            )

            # Create containers for each shelf that we can update
            shelves_ui = {}
            _shelves_ui_ref = shelves_ui  # Store globally for drag-drop handler
            for shelf in sorted_shelves:
                shelf_subentries = subentries_by_shelf[shelf]
                container = ui.column().classes("w-full gap-1")
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
