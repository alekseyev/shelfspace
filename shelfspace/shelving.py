"""Decides which shelf an unwatched item belongs on.

Carried over from the Trakt-era ``process-upcoming`` command, which recomputed
placement on every run so that episodes followed air-date changes and manual
Icebox moves automatically. That behaviour is worth keeping regardless of where
the episode data comes from, so it lives here rather than in the importer.
"""

from datetime import date

from bson import ObjectId

from shelfspace.models import Entry, Shelf

ICEBOX = "Icebox"
BACKLOG = "Backlog"


class ShelfPlacement:
    """Resolves air dates to shelves for one run of an import or refresh."""

    def __init__(self, shelves: dict[ObjectId, Shelf]):
        self.icebox = next(s for s in shelves.values() if s.name == ICEBOX)
        self.backlog = next(s for s in shelves.values() if s.name == BACKLOG)
        # Sorted so the first containing range wins deterministically.
        self.dated = sorted(
            (s for s in shelves.values() if s.start_date and s.end_date),
            key=lambda s: s.start_date,
        )

    def is_parked(self, entries: list[Entry]) -> bool:
        """Whether a show has been deliberately set aside in the Icebox.

        Parking is sticky across seasons: if any unwatched episode of any season
        sits in the Icebox, a newly announced season should not leapfrog it onto
        a dated shelf. Move every episode out of the Icebox to un-park a show.
        """
        return any(
            not subentry.is_finished and subentry.shelf_id == self.icebox.id
            for entry in entries
            for subentry in entry.subentries
        )

    def resolve(self, air_date: date | None, parked: bool = False) -> ObjectId:
        """The shelf an unwatched item should sit on, given when it airs."""
        if parked:
            return self.icebox.id

        if air_date:
            for shelf in self.dated:
                if shelf.start_date <= air_date <= shelf.end_date:
                    return shelf.id

        return self.backlog.id

    def reassign(self, entries: list[Entry], parked: bool) -> int:
        """Recompute the shelf of every unwatched subentry. Returns how many moved.

        Finished subentries are left alone -- they record which shelf the time
        was actually spent on.
        """
        moved = 0
        for entry in entries:
            for subentry in entry.subentries:
                if subentry.is_finished:
                    continue
                target = self.resolve(subentry.release_date, parked)
                if subentry.shelf_id != target:
                    subentry.shelf_id = target
                    moved += 1
        return moved
