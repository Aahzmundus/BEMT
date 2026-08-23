"""The one timestamp helper. Always timezone-aware UTC.

Hand-rolling ``strptime``-with-fallback in each module is how a codebase ends up
comparing a naive datetime against an aware one at 3am. Everything here goes
through these two functions.
"""

from __future__ import annotations

from datetime import datetime, timezone

DB_FORMAT = "%Y-%m-%d %H:%M:%S"


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_str() -> str:
    """Current UTC time in the format every timestamp column uses."""
    return now().strftime(DB_FORMAT)


def parse_ts(value: str | None) -> datetime | None:
    """Parse a DB timestamp or an ESI ISO-8601 string into aware UTC.

    Returns None for anything unparseable rather than raising: a broken
    timestamp should degrade a "last refreshed" label, never crash the page.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, DB_FORMAT)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_seconds(value: str | None) -> float | None:
    """Seconds since ``value``, or None if it can't be parsed."""
    dt = parse_ts(value)
    if dt is None:
        return None
    return (now() - dt).total_seconds()
