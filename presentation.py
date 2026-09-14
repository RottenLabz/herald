"""Bounded date presentation shared by Discord transports."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def format_published_at(value: str) -> str:
    value = (value or "").strip()

    if not value:
        return ""

    dt = None

    try:
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except Exception:
        dt = None

    if dt is None:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            dt = None

    if dt is None:
        return value[:100]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    try:
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except (ValueError, OverflowError, OSError):
        return value[:100]
