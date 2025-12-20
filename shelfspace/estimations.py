def estimation_from_minutes(val: int) -> int:
    """Round UP to 6 minutes"""
    val += 10 - (val % 10)
    return val


def estimate_book_from_pages(pages: int) -> int:
    return estimation_from_minutes(int(pages * 1.4))


def estimate_ed_book_from_pages(pages: int) -> int:
    return estimation_from_minutes(int(pages * 1.8))


def estimate_comic_book_from_pages(pages: int) -> int:
    return estimation_from_minutes(int(pages * 1.2))


def estimate_from_hltb(seconds: int) -> int:
    return estimation_from_minutes(seconds * 1.1 // 60)


def round_up_game_estimate(minutes: int) -> int:
    return estimation_from_minutes(int(minutes * 1.1))


def estimate_episode(val: int) -> int:
    val = int(val)
    val += 5
    val += 6 - (val % 6)
    return val
