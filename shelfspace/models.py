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
    name: str
    start_date: date | None = None
    end_date: date | None = None
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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
