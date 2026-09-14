from collections.abc import Callable

from provider_runtime import discover_sources, discovery_worker
from providers.common import error_code, escape_display_text

from storage import (
    WATCH_STATUS_HELD,
    WATCH_STATUS_PENDING,
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


def startup_mode_to_status(mode: str) -> str:
    mode = (mode or "").strip().lower()

    if mode == "pending":
        return WATCH_STATUS_PENDING

    if mode == "post":
        return WATCH_STATUS_PENDING

    if mode == "skip" or mode == "skipped":
        return WATCH_STATUS_SKIPPED

    return WATCH_STATUS_HELD


def safe_fetch_source(category: str, fetch_func: Callable[[], list[dict]]) -> tuple[list[dict], str]:
    try:
        items = fetch_func()
        if not isinstance(items, list):
            return [], "invalid_provider_result"
        return items, ""
    except Exception as exc:
        return [], error_code(exc)


def fetch_all_sources_with_errors() -> tuple[dict[str, list[dict]], list[str]]:
    result = discover_sources()
    sources = {}
    for item in result["items"]:
        sources.setdefault(item["category"], []).append(item)
    return sources, [record["error"] for record in result["health"] if record["error"]]


def fetch_all_sources() -> dict[str, list[dict]]:
    return fetch_all_sources_with_errors()[0]


def ingest_discovery(result: dict, queue_status: str = WATCH_STATUS_HELD) -> dict:
    stats = {"busy": bool(result.get("busy")), "seen": 0, "created": 0, "existing": 0, "held": 0, "pending": 0,
             "skipped": 0, "by_category": {}, "health": result.get("health", []),
             "errors": [record["error"] for record in result.get("health", []) if record["error"]]}
    if result.get("error"):
        stats["errors"].append(result["error"])
    for item in reversed(result.get("items", [])):
        stats["seen"] += 1
        category = item.get("category", "announcements")
        stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
        # Source policy applies to every category, including legacy categories.
        item_status = WATCH_STATUS_HELD if item.get("delivery_mode", "review") == "review" else queue_status
        try:
            stored = upsert_item(item, status=item_status)
            if stored.get("created"):
                stats["created"] += 1
                status = stored.get("status", "")
                stats[status] = stats.get(status, 0) + 1
            else:
                stats["existing"] += 1
        except Exception as exc:
            stats["errors"].append(error_code(exc))
    stats["errors"] = stats["errors"][:50]
    return stats


async def discover_items_async(queue_status: str = WATCH_STATUS_HELD) -> dict:
    result = await discovery_worker.run()
    return ingest_discovery(result, queue_status)


def discover_items(queue_status: str = WATCH_STATUS_HELD) -> dict:
    return ingest_discovery(discover_sources(), queue_status)


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
    tags = escape_display_text(item.get("tags") or "", 200)
    tag_text = f" | tags: {tags}" if tags else ""

    url = (item.get("url") or "").strip()

    if url:
        # Angle brackets keep the link clickable but stop Discord unfurl embeds.
        url = f"<{url}>"

    return (
        f"`{item.get('id')}` — **{escape_display_text(item.get('title', ''), 256)}**\n"
        f"source: {escape_display_text(item.get('source', ''), 100)} | category: {escape_display_text(item.get('category', ''), 64)}{tag_text}\n"
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
    lines = ["🎺 **Herald Watcher Status**", "",
             f"Discovery running: `{discovery_worker.running}`",
             f"Last successful source cycle: `{discovery_worker.last_success_at or 'never'}`", ""]
    for record in discovery_worker.last_health:
        lines.append(f"- {escape_display_text(record['name'], 100)}: `{record['status']}` ({record['item_count']})")
    for status in ("held", "pending", "sending", "uncertain", "posted", "failed", "skipped"):
        lines.append(f"{status}: `{count_items_by_status(status)}`")
    return "\n".join(lines)


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
    # Delivery is FIFO so old pending items cannot be starved by newer alerts.
    return list_items_by_status("pending", limit, newest_first=False)


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
        if isinstance(item_id, bool):
            continue

        if isinstance(item_id, int):
            parsed_id = item_id
        else:
            text = str(item_id).strip()

            if not text.isdecimal():
                continue

            parsed_id = int(text)

        if parsed_id <= 0:
            continue

        if parsed_id not in unique_ids:
            unique_ids.append(parsed_id)

    stats = {
        "requested": len(unique_ids),
        "skipped": 0,
        "not_found": [],
        "blocked": [],
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
            current = get_item_by_id(item_id)
            if current is None:
                stats["not_found"].append(item_id)
            else:
                stats["blocked"].append({"id": item_id, "status": current["status"]})

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

    count = end_id - start_id + 1

    if count > max_count:
        return {
            "requested": count,
            "skipped": 0,
            "not_found": [],
            "error": f"Range too large. Maximum is {max_count} items at once.",
        }

    return skip_items(list(range(start_id, end_id + 1)))


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

    for blocked in stats.get("blocked", [])[:25]:
        lines.append(f"Not skipped: item `{blocked['id']}` is `{blocked['status']}`; in-flight/final states require inspection.")
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


def retry_failed(max_attempts: int | None = None) -> int:
    return retry_failed_items(max_attempts)


def get_item(item_id: int) -> dict | None:
    return get_item_by_id(item_id)


def mark_posted(item_id: int, discord_message_id: str = "") -> bool:
    return mark_item_posted(item_id, discord_message_id)


def mark_failed(item_id: int, error: str) -> bool:
    return mark_item_failed(item_id, error)
