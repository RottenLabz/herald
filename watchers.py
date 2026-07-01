from collections.abc import Callable

from storage import (
    WATCH_STATUS_HELD,
    WATCH_STATUS_PENDING,
    WATCH_STATUS_FAILED,
    WATCH_STATUS_SKIPPED,
    count_items_by_category,
    count_items_by_status,
    get_item_by_id,
    list_items_by_status,
    mark_item_failed,
    mark_item_posted,
    retry_failed_items,
    set_item_status,
    upsert_item,
)
from providers import gamerpower, guru3d, security, twitch


def startup_mode_to_status(mode: str) -> str:
    mode = (mode or "").strip().lower()

    if mode == "pending":
        return WATCH_STATUS_PENDING

    if mode == "post":
        return WATCH_STATUS_PENDING

    if mode == "skip" or mode == "skipped":
        return WATCH_STATUS_SKIPPED

    return WATCH_STATUS_HELD


def safe_fetch_source(
    category: str,
    fetch_func: Callable[[], list[dict]],
) -> tuple[list[dict], str]:
    try:
        items = fetch_func()
    except Exception as exc:
        return [], f"{category}: {type(exc).__name__}: {exc}"

    if items is None:
        return [], ""

    if not isinstance(items, list):
        return [], f"{category}: provider returned {type(items).__name__}, expected list"

    return items, ""


def fetch_all_sources_with_errors() -> tuple[dict[str, list[dict]], list[str]]:
    provider_defs: list[tuple[str, Callable[[], list[dict]]]] = [
        ("free_games", gamerpower.fetch_items),
        ("gpu_updates", guru3d.fetch_items),
        ("stream_alerts", twitch.fetch_items),
        ("security_alerts", security.fetch_items),
    ]

    sources: dict[str, list[dict]] = {}
    errors: list[str] = []

    for category, fetch_func in provider_defs:
        items, error = safe_fetch_source(category, fetch_func)
        sources[category] = items

        if error:
            errors.append(error)

    return sources, errors


def fetch_all_sources() -> dict[str, list[dict]]:
    sources, _errors = fetch_all_sources_with_errors()
    return sources


def discover_items(queue_status: str = WATCH_STATUS_HELD) -> dict:
    sources, errors = fetch_all_sources_with_errors()

    stats = {
        "seen": 0,
        "created": 0,
        "existing": 0,
        WATCH_STATUS_HELD: 0,
        WATCH_STATUS_PENDING: 0,
        WATCH_STATUS_SKIPPED: 0,
        "by_category": {},
        "errors": errors,
    }

    for category, items in sources.items():
        stats["by_category"][category] = len(items)

        for item in reversed(items):
            stats["seen"] += 1

            item_status = queue_status

            # Security alerts start review-first, even during normal auto cycles.
            # They can still be posted manually with: herald post <id>
            if category == "security_alerts":
                item_status = WATCH_STATUS_HELD

            # Stream alerts are time-sensitive, so they should never sit in held.
            # If a watched Twitch channel is live, queue it for posting straight away.
            if category == "stream_alerts":
                item_status = WATCH_STATUS_PENDING

            result = upsert_item(item, status=item_status)

            if result.get("created"):
                stats["created"] += 1
                status = result.get("status") or ""
                stats[status] = stats.get(status, 0) + 1
            else:
                stats["existing"] += 1

    return stats


def format_discover_stats(stats: dict) -> str:
    by_category = stats.get("by_category") or {}

    lines = [
        "🎺 **Herald discovery complete**",
        "",
        f"Seen: `{stats.get('seen', 0)}`",
        f"Created: `{stats.get('created', 0)}`",
        f"Existing: `{stats.get('existing', 0)}`",
        f"Held: `{stats.get('held', 0)}`",
        f"Pending: `{stats.get('pending', 0)}`",
        f"Skipped: `{stats.get('skipped', 0)}`",
        "",
        "By category:",
    ]

    for category, count in sorted(by_category.items()):
        lines.append(f"- `{category}`: `{count}`")

    errors = stats.get("errors") or []

    if errors:
        lines.append("")
        lines.append("Errors:")
        for error in errors[:5]:
            lines.append(f"- {error}")

    return "\n".join(lines)


def format_item_line(item: dict) -> str:
    tags = item.get("tags") or ""
    tag_text = f" | tags: `{tags}`" if tags else ""

    url = (item.get("url") or "").strip()

    if url:
        # Angle brackets keep the link clickable but stop Discord unfurl embeds.
        url = f"<{url}>"

    return (
        f"`{item.get('id')}` — **{item.get('title', '')}**\n"
        f"source: `{item.get('source', '')}` | category: `{item.get('category', '')}`{tag_text}\n"
        f"{url}"
    )


def format_items(title: str, items: list[dict]) -> str:
    if not items:
        return f"🎺 **{title}**\n\nNo items."

    lines = [f"🎺 **{title}**", ""]

    for item in items:
        lines.append(format_item_line(item))
        lines.append("")

    return "\n".join(lines).strip()


def watcher_status_text() -> str:
    return (
        "🎺 **Herald Watcher Status**\n\n"
        f"Held: `{count_items_by_status('held')}`\n"
        f"Pending: `{count_items_by_status('pending')}`\n"
        f"Posted: `{count_items_by_status('posted')}`\n"
        f"Failed: `{count_items_by_status('failed')}`\n"
        f"Skipped: `{count_items_by_status('skipped')}`\n\n"
        f"Free game items: `{count_items_by_category('free_games')}`\n"
        f"GPU update items: `{count_items_by_category('gpu_updates')}`\n"
        f"Stream alert items: `{count_items_by_category('stream_alerts')}`\n"
        f"Security alert items: `{count_items_by_category('security_alerts')}`"
    )


def held_items_text(limit: int = 10) -> str:
    return format_items("Held Herald items", list_items_by_status("held", limit))


def pending_items_text(limit: int = 10) -> str:
    return format_items("Pending Herald items", list_items_by_status("pending", limit))


def posted_items_text(limit: int = 10) -> str:
    return format_items("Recently posted Herald items", list_items_by_status("posted", limit))


def failed_items_text(limit: int = 10) -> str:
    return format_items("Failed Herald items", list_items_by_status("failed", limit))


def held_items(limit: int = 10) -> list[dict]:
    return list_items_by_status("held", limit)


def pending_items(limit: int = 10) -> list[dict]:
    return list_items_by_status("pending", limit)


def skip_item(item_id: int) -> bool:
    return set_item_status(
        item_id,
        WATCH_STATUS_SKIPPED,
        reason="manual_skip",
        actor="owner",
    )


def skip_items(item_ids: list[int]) -> dict:
    unique_ids = []

    for item_id in item_ids:
        try:
            item_id = int(item_id)
        except Exception:
            continue

        if item_id <= 0:
            continue

        if item_id not in unique_ids:
            unique_ids.append(item_id)

    stats = {
        "requested": len(unique_ids),
        "skipped": 0,
        "not_found": [],
    }

    for item_id in unique_ids:
        if set_item_status(
            item_id,
            WATCH_STATUS_SKIPPED,
            reason="manual_skip_batch",
            actor="owner",
        ):
            stats["skipped"] += 1
        else:
            stats["not_found"].append(item_id)

    return stats


def skip_range(start_id: int, end_id: int, max_count: int = 200) -> dict:
    start_id = int(start_id)
    end_id = int(end_id)

    if start_id <= 0 or end_id <= 0:
        return {
            "requested": 0,
            "skipped": 0,
            "not_found": [],
            "error": "IDs must be positive numbers.",
        }

    if end_id < start_id:
        start_id, end_id = end_id, start_id

    item_ids = list(range(start_id, end_id + 1))

    if len(item_ids) > max_count:
        return {
            "requested": len(item_ids),
            "skipped": 0,
            "not_found": [],
            "error": f"Range too large. Maximum is {max_count} items at once.",
        }

    return skip_items(item_ids)


def skip_held_items(limit: int | None = None, max_count: int = 500) -> dict:
    if limit is None:
        limit = max_count

    limit = int(limit)

    if limit <= 0:
        return {
            "requested": 0,
            "skipped": 0,
            "not_found": [],
            "error": "Limit must be greater than zero.",
        }

    if limit > max_count:
        return {
            "requested": limit,
            "skipped": 0,
            "not_found": [],
            "error": f"Too many held items requested. Maximum is {max_count}.",
        }

    items = list_items_by_status("held", limit)
    item_ids = [int(item.get("id")) for item in items if item.get("id")]

    stats = skip_items(item_ids)
    stats["held_checked"] = len(items)
    return stats


def format_skip_stats(stats: dict) -> str:
    if stats.get("error"):
        return f"Could not skip items: `{stats['error']}`"

    not_found = stats.get("not_found") or []
    lines = [
        "🎺 **Herald skip complete**",
        "",
        f"Requested: `{stats.get('requested', 0)}`",
        f"Skipped: `{stats.get('skipped', 0)}`",
    ]

    if "held_checked" in stats:
        lines.append(f"Held checked: `{stats.get('held_checked', 0)}`")

    if not_found:
        preview = ", ".join(str(x) for x in not_found[:25])
        extra = "" if len(not_found) <= 25 else f" and {len(not_found) - 25} more"
        lines.append(f"Not found: `{preview}{extra}`")

    return "\n".join(lines)


def hold_item(item_id: int) -> bool:
    return set_item_status(
        item_id,
        WATCH_STATUS_HELD,
        reason="manual_hold",
        actor="owner",
    )


def promote_item(item_id: int) -> bool:
    return set_item_status(
        item_id,
        WATCH_STATUS_PENDING,
        reason="manual_promote",
        actor="owner",
    )


def retry_failed() -> int:
    return retry_failed_items()


def get_item(item_id: int) -> dict | None:
    return get_item_by_id(item_id)


def mark_posted(item_id: int, discord_message_id: str = "") -> bool:
    return mark_item_posted(item_id, discord_message_id)


def mark_failed(item_id: int, error: str) -> bool:
    return mark_item_failed(item_id, error)
