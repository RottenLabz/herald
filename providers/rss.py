"""Generic operator-configured RSS/Atom; no bundled third-party feed list."""
from __future__ import annotations

import time


from providers.common import (MAX_ENTRIES, OPERATION_TIMEOUT, clean_text, error_code,
                              fetch_bytes, health, normalize_item)


def parse_feed(body: bytes):
    import feedparser
    return feedparser.parse(body)


def fetch_source(source: dict, *, deadline=None) -> dict:
    if not source["enabled"]:
        return {"items": [], "health": health(source, "disabled")}
    deadline = deadline or (time.monotonic() + OPERATION_TIMEOUT)
    try:
        body = fetch_bytes(source["url"], deadline=deadline)
        # The parser receives bounded bytes, never a remote URL or response stream.
        parsed = parse_feed(body)
        if not parsed.get("version") and not parsed.get("entries"):
            return {"items": [], "health": health(source, "failed", error="unrecognized_feed")}
        items, invalid = [], 0
        entries = parsed.get("entries", [])
        for entry in entries[:MAX_ENTRIES]:
            if time.monotonic() > deadline:
                return {"items": items, "health": health(source, "degraded" if items else "failed", len(items), "operation_deadline_exceeded")}
            try:
                image_url = ""
                for key in ("media_thumbnail", "media_content"):
                    values = entry.get(key) or []
                    if isinstance(values, list) and values and isinstance(values[0], dict):
                        image_url = values[0].get("url", "")
                        if image_url:
                            break
                tags = entry.get("tags") or []
                tags = [t.get("term", "") for t in tags[:16] if isinstance(t, dict)] if isinstance(tags, list) else []
                raw = {"title": entry.get("title", ""), "url": entry.get("link", ""),
                       "summary": entry.get("summary") or entry.get("description") or "",
                       "external_id": entry.get("id") or entry.get("guid") or entry.get("link", ""),
                       "published_at": entry.get("published") or entry.get("updated") or "",
                       "tags": tags, "image_url": image_url}
                items.append(normalize_item(raw, source))
            except (ValueError, TypeError, AttributeError):
                invalid += 1
        issues = []
        if parsed.get("bozo"):
            issues.append("parser_recovered" if items else "parser_failed")
        if len(entries) > MAX_ENTRIES:
            issues.append("entry_limit_reached")
        if invalid:
            issues.append("invalid_entries")
        status = "degraded" if issues and items else "failed" if issues else "healthy" if items else "empty"
        return {"items": items, "health": health(source, status, len(items), ",".join(issues))}
    except Exception as exc:
        return {"items": [], "health": health(source, "failed", error=error_code(exc))}
