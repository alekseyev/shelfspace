from shelfspace.models import MediaType


def estimation_from_minutes(val: int) -> int:
    """Round UP to 6 minutes"""
    val += 10 - (val % 10)
    return val


def estimate_book_from_pages(pages: int) -> int:
    return estimation_from_minutes(int(pages * 1.4))


def estimate_ed_book_from_pages(pages: int) -> int:
    return estimation_from_minutes(int(pages * 1.8))


def estimate_comic_book_from_pages(pages: int) -> int:
    return estimation_from_minutes(int(pages * 0.9))


def estimate_book(media_type: MediaType, pages: int | None) -> int | None:
    """Reading time for a book, at the rate its media type implies.

    Returns None when the page count is unknown, which is common for books that
    have not been released yet.
    """
    if not pages:
        return None
    if media_type == MediaType.BOOK_COM:
        return estimate_comic_book_from_pages(pages)
    if media_type == MediaType.BOOK_ED:
        return estimate_ed_book_from_pages(pages)
    return estimate_book_from_pages(pages)


def estimate_from_hltb(seconds: int) -> int:
    return estimation_from_minutes(seconds * 1.1 // 60)


def round_up_game_estimate(minutes: int) -> int:
    return estimation_from_minutes(int(minutes * 1.1))


def estimate_episode(val: int) -> int:
    val = int(val)
    val += 5
    val += 6 - (val % 6)
    return val
