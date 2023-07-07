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
    BOOK = "Book"
    BOOK_ED = "Book (educational)"
    BOOK_COM = "Book (comics)"
    ART = "Article"
    VID = "Video"


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
