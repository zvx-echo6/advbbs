"""
advBBS Formatting Utilities

Helper functions for formatting output.
"""

import time
from datetime import datetime


def format_timestamp(timestamp_us: int) -> str:
    """
    Format microsecond timestamp to human-readable string.

    Args:
        timestamp_us: Microseconds since epoch

    Returns:
        Formatted string like "2025-12-10 14:32"
    """
    if not timestamp_us:
        return "Never"

    timestamp_s = timestamp_us / 1_000_000
    dt = datetime.fromtimestamp(timestamp_s)
    return dt.strftime("%Y-%m-%d %H:%M")


def format_uptime(start_time: float) -> str:
    """
    Format uptime from start timestamp.

    Args:
        start_time: Unix timestamp of start

    Returns:
        Formatted string like "2d 5h 30m"
    """
    if not start_time:
        return "Unknown"

    elapsed = int(time.time() - start_time)

    days = elapsed // 86400
    hours = (elapsed % 86400) // 3600
    minutes = (elapsed % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")

    return " ".join(parts)


def format_time(timestamp: float) -> str:
    """
    Format Unix timestamp to human-readable string.

    Args:
        timestamp: Unix timestamp (seconds)

    Returns:
        Formatted string like "14:32:15"
    """
    if not timestamp:
        return "Never"

    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%H:%M:%S")


def format_size(size_bytes: int) -> str:
    """
    Format byte size to human-readable string.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string like "1.5 KB"
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def truncate(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """
    Truncate text to maximum length.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to add if truncated

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text

    return text[:max_length - len(suffix)] + suffix


def pad_right(text: str, width: int) -> str:
    """Pad text to width with spaces on the right."""
    return text.ljust(width)[:width]


def pad_left(text: str, width: int) -> str:
    """Pad text to width with spaces on the left."""
    return text.rjust(width)[:width]
