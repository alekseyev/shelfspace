def estimation_from_minutes(val: int) -> float:
    """Round UP to 6 minutes, convert to hours"""
    val += 6 - (val % 6)
    return val / 60


def estimate_book_from_pages(pages: int) -> float:
    return estimation_from_minutes(int(pages * 1.4))


def estimate_ed_book_from_pages(pages: int) -> float:
    return estimation_from_minutes(int(pages * 2))


def estimate_comic_book_from_pages(pages: int) -> float:
    return estimation_from_minutes(int(pages * 1.5))


def estimate_from_hltb(seconds: int) -> float:
    return estimation_from_minutes(seconds * 1.1 // 60)


def estimate_episode(val: int) -> int:
    val = int(val)
    val += 5
    val += 6 - (val % 6)
    return val
