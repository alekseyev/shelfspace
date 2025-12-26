from datetime import date, datetime
import enum
from beanie import Document
from typing import Optional
from bson import ObjectId
from pydantic import BaseModel, Field

from shelfspace.utils import format_minutes


"""
Entries are the main objects in the database.
They are used to store the information about the media that you are tracking. Entry may represent a project, a course, a movie, a series, a game, a book, an article, a talk/video, etc.

Subentries are the sub-objects of an entry. They may represent a chapter, an episode, or a chunk of a media, or even multiple views of the same movie, particularly when you want to put parts of a single entry into different shelves.

Shelves are the shelves that you can put your subentries into. Default shelves are backlog and icebox for unstarted entries. Other shelves have start and end dates (so they are like a sprint in a kanban board).
"""


class MediaType(str, enum.Enum):
    PROJECTS = "Projects"
    COURSE = "Course"
    MOVIE = "Movie"
    SERIES = "Series"
    GAME = "Game"
    GAME_VR = "Game (VR)"
    GAME_MOBILE = "Game (mobile)"
    BOOK = "Book"
    BOOK_ED = "Book (educational)"
    BOOK_COM = "Book (comics)"
    ART = "Article"
    VID = "Talk/video"


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


class Status(str, enum.Enum):
    FUTURE = "FUTURE"
    CURRENT = "CURRENT"
    DONE = "DONE"


class LegacyEntry(BaseModel):
    type: MediaType
    name: str
    notes: str = ""
    estimated: Optional[float] = None
    spent: Optional[float] = None
    prog: str = ""
    status: Optional[Status] = None
    release_date: Optional[str] = ""
    rating: Optional[int] = None
    metadata: Optional[dict] = {}


class Shelf(Document):
    name: str = ""
    start_date: date | None = None
    end_date: date | None = None
    description: str = ""
    is_finished: bool = False
    weight: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    async def get_shelves_dict(cls) -> dict[str, "Shelf"]:
        shelves = await cls.find(cls.is_finished == False).to_list()  # noqa: E712
        cls._shelves_dict = {shelf.id: shelf for shelf in shelves}
        return cls._shelves_dict

    def generate_name(self) -> str:
        if self.start_date and self.end_date:
            return f"{self.start_date.strftime('%d %B')} - {self.end_date.strftime('%d %B %Y')}"
        return self.name

    async def save(self) -> None:
        self.updated_at = datetime.utcnow()
        if self.start_date and self.end_date:
            self.name = self.generate_name()
            self.weight = self.start_date.toordinal()
        elif self.name == "Upcoming":
            self.weight = 1000000
        elif self.name == "Backlog":
            self.weight = 2000000
        elif self.name == "Icebox":
            self.weight = 3000000
        await super().save()

    def __str__(self) -> str:
        return f"Shelf {self.name}{' (finished)' if self.is_finished else ''}"


class SubEntry(BaseModel):
    shelf: str | ObjectId = ""
    shelf_id: ObjectId | None = None  # Reference to Shelf document
    name: str = ""
    estimated: int | None = None
    spent: int | None = 0
    is_finished: bool = False
    release_date: date | None = None
    metadata: dict = {}

    @property
    def shelf_name(self) -> str:
        if hasattr(Shelf, "_shelves_dict"):
            if self.shelf_id in Shelf._shelves_dict:
                return Shelf._shelves_dict[self.shelf_id].name
        return str(self.shelf_id)

    def __str__(self) -> str:
        return (
            f"Subentry{' ' + self.name if self.name else ''} @{self.shelf_name} - "
            f"{format_minutes(self.spent)}/{format_minutes(self.estimated)}"
            f"{' (finished)' if self.is_finished else ''}"
        )

    class Config:
        arbitrary_types_allowed = True


class Entry(Document):
    type: MediaType
    name: str
    notes: str = ""
    release_date: date | None = None
    rating: int | None = None
    metadata: dict = {}
    links: list[str] = []
    subentries: list[SubEntry] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def __str__(self) -> str:
        shelves = list(set([subentry.shelf_name for subentry in self.subentries]))
        total_estimated = sum(
            subentry.estimated for subentry in self.subentries if subentry.estimated
        )
        total_spent = (
            sum(subentry.spent for subentry in self.subentries if subentry.spent) or 0
        )
        is_finished = all(subentry.is_finished for subentry in self.subentries)
        return (
            f"Entry {get_emoji_for_type(self.type)} {self.name} @{', '.join(shelves)} - "
            f"{format_minutes(total_spent)}/{format_minutes(total_estimated)}"
            f"{' (finished)' if is_finished else ''}"
        )


beanie_models = [Entry, Shelf]  # Used for Beanie initialization
