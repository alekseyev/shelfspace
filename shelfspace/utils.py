"""Utility functions for formatting and common operations."""

from datetime import datetime
import re


def format_minutes(minutes: int | None) -> str:
    """
    Format minutes into a human-readable hours:minutes string.

    Args:
        minutes: Total minutes to format.

    Returns:
        Formatted string in the format "Xh Ym" (e.g., "2h 5m").
    """
    if minutes is None:
        return "N/A"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins}m"


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_date(text: str):
    """
    Parse dates like 'October 28th, 2021' into datetime.date
    """
    text = text.lower().replace(",", "")

    parts = text.split()
    if len(parts) != 3:
        return None

    month = MONTHS.get(parts[0])
    day = parts[1]
    day = re.sub(r"(st|nd|rd|th)", "", day)
    day = int(day)
    year = int(parts[2])

    return datetime(year, month, day).date()
