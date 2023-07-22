def estimation_from_minutes(val: int) -> float:
    """Add 5 minutes, round UP to 15 minutes, convert to hours"""
    val += 5
    if val < 600:
        val += 15 - (val % 15)
    else:
        val += 60 - (val % 60)
    return val / 60


def estimate_book_from_pages(pages: int) -> float:
    return estimation_from_minutes(int(pages * 1.4))


def estimate_ed_book_from_pages(pages: int) -> float:
    return estimation_from_minutes(int(pages * 2))


def estimate_comic_book_from_pages(pages: int) -> float:
    return estimation_from_minutes(int(pages * 1.5))


def estimate_from_hltb(seconds: int) -> float:
    return estimation_from_minutes(seconds * 1.1 // 60)
