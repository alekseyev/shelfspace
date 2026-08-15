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
        self.by_id = dict(shelves)
        self.icebox = next(s for s in shelves.values() if s.name == ICEBOX)
        self.backlog = next(s for s in shelves.values() if s.name == BACKLOG)
        # A finished sprint is closed: nothing may be scheduled onto it, however
        # well its dates fit. Sorted so the first containing range wins.
        self.dated = sorted(
            (
                s
                for s in shelves.values()
                if s.start_date and s.end_date and not s.is_finished
            ),
            key=lambda s: s.start_date,
        )

    def is_parked(self, entries: list[Entry]) -> bool:
        """Whether any of these seasons has been set aside in the Icebox.

        Only ever decides where *new* episodes go: an episode invented by a
        refresh has no placement of its own, and one belonging to a season being
        held back should not be scheduled onto a dated shelf ahead of the
        seasons before it. Episodes that already have a shelf are governed by
        ``reassign``, which never sweeps a show into the Icebox wholesale.

        Which seasons count as "these" is the caller's decision, because icing a
        season means "not until I have caught up" -- it carries forward to later
        seasons but never back to earlier ones.
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

    def _is_forward(self, current_id: ObjectId | None, target_id: ObjectId) -> bool:
        """Whether moving to ``target_id`` moves the item later, never earlier.

        Air dates are a prediction of when something can be watched, not an
        instruction about when it must be: an episode still sitting unwatched
        weeks after it aired, or deliberately pushed out to be binged once the
        season finishes, has been placed on purpose. So a slipped date may push
        an episode back, but nothing pulls one forward again.
        """
        current = self.by_id.get(current_id)
        # The Backlog is "unscheduled", not a point in time, so giving something
        # a date for the first time is not a move backwards.
        if current is None or not current.start_date:
            return True

        target = self.by_id.get(target_id)
        # Backlog and Icebox sit outside the calendar rather than later in it,
        # so falling back to one is never a reason to unschedule a sprint item.
        if target is None or not target.start_date:
            return False

        return target.start_date > current.start_date

    def reassign(self, entries: list[Entry]) -> int:
        """Recompute the shelf of every unwatched subentry. Returns how many moved.

        Two things are left alone. Finished subentries record which shelf the
        time was actually spent on. The Icebox is a hold someone put there by
        hand, so nothing is taken out of it -- and nothing is put in either,
        which is the difference between holding a season back and burying the
        whole show: icing a later season must not drag the earlier ones out of
        the Backlog behind it.
        """
        moved = 0
        for entry in entries:
            for subentry in entry.subentries:
                if subentry.is_finished or subentry.shelf_id == self.icebox.id:
                    continue
                target = self.resolve(subentry.release_date)
                if subentry.shelf_id == target:
                    continue
                if not self._is_forward(subentry.shelf_id, target):
                    continue
                subentry.shelf_id = target
                moved += 1
        return moved
