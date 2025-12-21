from datetime import date, datetime
import enum
from beanie import Document, Indexed
from typing import Optional
from pydantic import BaseModel, Field


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

    async def get_shelves_dict() -> dict[str, "Shelf"]:
        shelves = await Shelf.find(Shelf.is_finished == False).to_list()  # noqa: E712
        return {shelf.name: shelf for shelf in shelves}

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
    shelf: Indexed(str) = ""
    name: str = ""
    estimated: int | None = None
    spent: int | None = 0
    is_finished: bool = False
    release_date: date | None = None
    metadata: dict = {}


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


beanie_models = [Entry]
