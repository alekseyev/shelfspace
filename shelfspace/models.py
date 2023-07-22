from datetime import date
import enum
from typing import Optional
from pydantic import BaseModel


class MediaType(str, enum.Enum):
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


class Entry(BaseModel):
    type: MediaType
    name: str
    notes: str = ""
    estimated: Optional[float] = None
    spent: Optional[float] = None
    prog: str = ""
    status: Optional[Status] = None
    release_date: Optional[date] = None
