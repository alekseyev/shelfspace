from fastapi import Request
from nicegui import app, ui
from nicegui.events import ValueChangeEventArguments

from shelfspace.app_ctx import AppCtx
from shelfspace.models import Entry, SubEntry
from shelfspace.utils import format_minutes


# Define required shelves that should always be shown
REQUIRED_SHELVES = ["Backlog", "Icebox"]
DEFAULT_SHELF = "Icebox"

# Global reference to shelves_ui for drag-drop handling
_shelves_ui_ref: dict = {}


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


async def load_subentries() -> dict[str, list[tuple[Entry, SubEntry]]]:
    """
    Load all subentries from the database grouped by shelf.

    Returns a dict mapping shelf names to lists of (entry, subentry) tuples.
    """
    await AppCtx.ensure_initialized()
    entries = await Entry.find().to_list()

    # Group subentries by shelf
    subentries_by_shelf: dict[str, list[tuple[Entry, SubEntry]]] = {}
    for entry in entries:
        for subentry in entry.subentries:
            shelf = subentry.shelf or DEFAULT_SHELF
            if shelf not in subentries_by_shelf:
                subentries_by_shelf[shelf] = []
            subentries_by_shelf[shelf].append((entry, subentry))

    # Ensure required shelves are present (even if empty)
    for shelf in REQUIRED_SHELVES:
        if shelf not in subentries_by_shelf:
            subentries_by_shelf[shelf] = []

    return subentries_by_shelf


def get_all_shelves() -> list[str]:
    """Get all possible shelf options."""
    return sorted(REQUIRED_SHELVES)


async def update_subentry_shelf(
    entry_id: str, subentry_name: str, new_shelf: str, shelves_ui: dict
) -> None:
    """Update a subentry's shelf and refresh affected shelf containers."""
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

    old_shelf = subentry.shelf or DEFAULT_SHELF
    if old_shelf == new_shelf:
        return  # No change needed

    subentry.shelf = new_shelf
    await entry_obj.save()

    # Reload subentries to update UI
    subentries_by_shelf = await load_subentries()

    # Update both old and new shelf containers if they exist
    shelves_to_update = {old_shelf, new_shelf}
    for shelf in shelves_to_update:
        if shelf in shelves_ui:
            container_ref = shelves_ui[shelf]
            # Clear and rebuild the container
            container_ref.clear()
            shelf_subentries = subentries_by_shelf.get(shelf, [])
            build_shelf_content(shelf, shelf_subentries, shelves_ui, container_ref)


async def handle_drop(entry_id: str, subentry_name: str, target_shelf: str) -> None:
    """Handle a drag-drop event to move subentry to a new shelf."""
    global _shelves_ui_ref
    await update_subentry_shelf(entry_id, subentry_name, target_shelf, _shelves_ui_ref)


# REST endpoint for drag-drop operations
@app.post("/api/move-entry")
async def move_entry_api(request: Request):
    """API endpoint for moving entries between shelves via drag-drop."""
    data = await request.json()
    entry_id = data.get("entry_id")
    subentry_name = data.get("subentry_name", "")
    target_shelf = data.get("target_shelf")

    if not entry_id or not target_shelf:
        return {"success": False, "error": "Missing required fields"}

    try:
        entry_obj = await Entry.get(entry_id)
        if not entry_obj:
            return {"success": False, "error": "Entry not found"}

        # Find the subentry and update its shelf
        subentry = None
        for se in entry_obj.subentries:
            if se.name == subentry_name:
                subentry = se
                break

        if not subentry:
            return {"success": False, "error": "Subentry not found"}

        old_shelf = subentry.shelf or DEFAULT_SHELF
        if old_shelf == target_shelf:
            return {"success": True, "message": "No change needed"}

        subentry.shelf = target_shelf
        await entry_obj.save()
        return {"success": True, "old_shelf": old_shelf, "new_shelf": target_shelf}
    except Exception as e:
        return {"success": False, "error": str(e)}


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

        # Create the drop zone for this shelf
        drop_zone = ui.column().classes(
            "w-full p-3 border-2 border-dashed border-gray-300 rounded-lg bg-gray-50 gap-2 min-h-[80px] shelf-drop-zone"
        )
        drop_zone._props["data-shelf"] = shelf

        with drop_zone:
            if not subentries:
                ui.label("Drop entries here").classes(
                    "text-center text-gray-400 py-4 italic w-full"
                )
            else:
                for entry, subentry in sorted(
                    subentries, key=lambda x: x[1].name or x[0].name
                ):
                    create_subentry_card(entry, subentry, shelves_ui)


def create_subentry_card(entry: Entry, subentry: SubEntry, shelves_ui: dict) -> None:
    """Create a card for a subentry with shelf selector."""
    entry_id = str(entry.id)
    subentry_name_val = subentry.name or ""

    card = ui.card().classes("w-full draggable-card")
    card._props["draggable"] = "true"
    card._props["data-entry-id"] = entry_id
    card._props["data-subentry-name"] = subentry_name_val

    with card:
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
                current_shelf = subentry.shelf or DEFAULT_SHELF
                entry_id_captured = str(entry.id)
                subentry_name_captured = subentry.name

                # Define callback that captures entry_id and subentry_name separately
                def make_callback(eid: str, sename: str, ui_ref: dict):
                    async def on_shelf_selected(e: ValueChangeEventArguments):
                        await update_subentry_shelf(eid, sename, e.value, ui_ref)

                    return on_shelf_selected

                ui.select(
                    options=get_all_shelves(),
                    value=current_shelf,
                    on_change=make_callback(
                        entry_id_captured, subentry_name_captured, shelves_ui
                    ),
                ).props("dense outlined")


async def setup_ui():
    """Setup the main UI with all subentries grouped by shelf."""
    global _shelves_ui_ref
    subentries_by_shelf = await load_subentries()

    # Add drag-and-drop styles and JavaScript
    ui.add_head_html(
        """
        <style>
        .draggable-card {
            cursor: grab;
            opacity: 1;
            transition: opacity 0.2s, transform 0.2s;
        }
        .draggable-card:active {
            cursor: grabbing;
        }
        .draggable-card.dragging {
            opacity: 0.5;
            transform: scale(0.98);
        }
        .shelf-drop-zone {
            transition: background-color 0.2s, border-color 0.2s;
        }
        .shelf-drop-zone.drag-over {
            background-color: #dbeafe !important;
            border-color: #3b82f6 !important;
        }
        </style>
        """
    )

    # Add JavaScript for drag-drop handling
    ui.add_body_html(
        """
        <script>
        // Store drag data globally since dataTransfer is not always accessible
        window.dragData = null;

        document.addEventListener('dragstart', function(e) {
            const card = e.target.closest('.draggable-card');
            if (card) {
                card.classList.add('dragging');
                const entryId = card.getAttribute('data-entry-id');
                const subentryName = card.getAttribute('data-subentry-name');
                window.dragData = { entry_id: entryId, subentry_name: subentryName };
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', JSON.stringify(window.dragData));
            }
        });

        document.addEventListener('dragend', function(e) {
            const card = e.target.closest('.draggable-card');
            if (card) {
                card.classList.remove('dragging');
            }
            // Remove drag-over styling from all drop zones
            document.querySelectorAll('.shelf-drop-zone').forEach(zone => {
                zone.classList.remove('drag-over');
            });
        });

        document.addEventListener('dragover', function(e) {
            const dropZone = e.target.closest('.shelf-drop-zone');
            if (dropZone && window.dragData) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                dropZone.classList.add('drag-over');
            }
        });

        document.addEventListener('dragleave', function(e) {
            const dropZone = e.target.closest('.shelf-drop-zone');
            if (dropZone) {
                // Only remove if we're actually leaving the drop zone
                const rect = dropZone.getBoundingClientRect();
                if (e.clientX < rect.left || e.clientX > rect.right ||
                    e.clientY < rect.top || e.clientY > rect.bottom) {
                    dropZone.classList.remove('drag-over');
                }
            }
        });

        document.addEventListener('drop', async function(e) {
            const dropZone = e.target.closest('.shelf-drop-zone');
            if (dropZone && window.dragData) {
                e.preventDefault();
                dropZone.classList.remove('drag-over');
                const targetShelf = dropZone.getAttribute('data-shelf');
                if (targetShelf && window.dragData.entry_id) {
                    // Call the API endpoint to move the entry
                    try {
                        const response = await fetch('/api/move-entry', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({
                                entry_id: window.dragData.entry_id,
                                subentry_name: window.dragData.subentry_name,
                                target_shelf: targetShelf
                            })
                        });
                        const result = await response.json();
                        if (result.success) {
                            // Reload the page to reflect changes
                            location.reload();
                        } else {
                            console.error('Move failed:', result.error);
                        }
                    } catch (error) {
                        console.error('API call failed:', error);
                    }
                }
                window.dragData = null;
            }
        });
        </script>
        """
    )

    with ui.column().classes("w-full max-w-6xl mx-auto p-4"):
        ui.label("📚 All Entries").classes("text-3xl font-bold mb-6")

        if not subentries_by_shelf:
            ui.label("No entries found.").classes("text-lg text-gray-500")
        else:
            # Sort shelves with required shelves first
            shelves = sorted(subentries_by_shelf.keys())
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
