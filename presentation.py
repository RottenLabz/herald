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




def escape_provider_text(value: str, limit: int = 500) -> str:
    """Escape every Markdown delimiter before adding bot-owned formatting."""
    import re
    text = str(value or "")[:limit * 2]
    text = re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]", " ", text)
    text = text.replace("@", "@\u200b")
    text = " ".join(text.split())
    output = []
    size = 0
    for ch in text:
        escaped = "\\" + ch if ch in "\\`*_{}[]()#+-.!|>~" else ch
        if size + len(escaped) > limit:
            break
        output.append(escaped)
        size += len(escaped)
    return "".join(output)
