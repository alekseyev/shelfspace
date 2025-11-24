"""Utility functions for formatting and common operations."""


def format_minutes(minutes: int) -> str:
    """
    Format minutes into a human-readable hours:minutes string.

    Args:
        minutes: Total minutes to format.

    Returns:
        Formatted string in the format "Xh Ym" (e.g., "2h 5m").
    """
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
