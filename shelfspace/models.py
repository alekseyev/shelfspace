from datetime import date, datetime
import enum
from beanie import Document, Indexed
from typing import Optional
from pydantic import BaseModel, Field


class MediaType(str, enum.Enum):
    PROJECTS = "Projects"
    DUO = "Duolingo"
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


class Entry(Document):
    type: MediaType
    name: str
    notes: str = ""
    estimated: int | None = None
    spent: int | None = None
    status: Status | None = None
    release_date: date | None = None
    rating: int | None = None
    metadata: dict = {}
    shelf: Indexed(str) = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
