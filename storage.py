import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import HERALD_DB_PATH


WATCH_STATUS_HELD = "held"
WATCH_STATUS_PENDING = "pending"
WATCH_STATUS_POSTED = "posted"
WATCH_STATUS_FAILED = "failed"
WATCH_STATUS_SKIPPED = "skipped"

VALID_WATCH_STATUSES = {
    WATCH_STATUS_HELD,
    WATCH_STATUS_PENDING,
    WATCH_STATUS_POSTED,
    WATCH_STATUS_FAILED,
    WATCH_STATUS_SKIPPED,
}


def _db_path() -> Path:
    path = Path(HERALD_DB_PATH).expanduser()

    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def normalise_status(status: str) -> str:
    status = (status or "").strip().lower()

    if status in VALID_WATCH_STATUSES:
        return status

    return WATCH_STATUS_HELD


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dumps(value) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _last_audit_event_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        """
        SELECT event_hash
        FROM herald_audit_events
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        return ""

    return str(row["event_hash"] or "")


def _make_audit_event_hash(
    *,
    item_id: int | None,
    event_type: str,
    actor: str,
    old_status: str,
    new_status: str,
    reason: str,
    detail: str,
    payload_hash: str,
    previous_event_hash: str,
    created_at: str,
) -> str:
    canonical = _json_dumps(
        {
            "item_id": item_id,
            "event_type": event_type,
            "actor": actor,
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
            "detail": detail,
            "payload_hash": payload_hash,
            "previous_event_hash": previous_event_hash,
            "created_at": created_at,
        }
    )
    return _sha256_text(canonical)


def _insert_audit_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    item_id: int | None = None,
    actor: str = "herald",
    old_status: str = "",
    new_status: str = "",
    reason: str = "",
    detail: str = "",
    payload: dict | None = None,
) -> str:
    event_type = (event_type or "").strip()
    actor = (actor or "herald").strip()
    old_status = (old_status or "").strip()
    new_status = (new_status or "").strip()
    reason = (reason or "").strip()
    detail = (detail or "").strip()

    if len(detail) > 1000:
        detail = detail[:1000].rstrip() + "..."

    payload_json = _json_dumps(payload or {})
    payload_hash = _sha256_text(payload_json)
    previous_event_hash = _last_audit_event_hash(conn)
    created_at = _utc_now_text()

    event_hash = _make_audit_event_hash(
        item_id=item_id,
        event_type=event_type,
        actor=actor,
        old_status=old_status,
        new_status=new_status,
        reason=reason,
        detail=detail,
        payload_hash=payload_hash,
        previous_event_hash=previous_event_hash,
        created_at=created_at,
    )

    conn.execute(
        """
        INSERT INTO herald_audit_events (
            item_id,
            event_type,
            actor,
            old_status,
            new_status,
            reason,
            detail,
            payload_json,
            payload_hash,
            previous_event_hash,
            event_hash,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            event_type,
            actor,
            old_status,
            new_status,
            reason,
            detail,
            payload_json,
            payload_hash,
            previous_event_hash,
            event_hash,
            created_at,
        ),
    )

    return event_hash


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS herald_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                guild_id TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS herald_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'held',
                external_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                feed_url TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                discord_message_id TEXT NOT NULL DEFAULT '',
                post_attempts_count INTEGER NOT NULL DEFAULT 0,
                last_post_attempt TEXT NOT NULL DEFAULT '',
                last_post_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS herald_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'herald',
                old_status TEXT NOT NULL DEFAULT '',
                new_status TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '',
                payload_hash TEXT NOT NULL DEFAULT '',
                previous_event_hash TEXT NOT NULL DEFAULT '',
                event_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS herald_integrity_roots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_id TEXT NOT NULL UNIQUE,
                scope TEXT NOT NULL DEFAULT 'herald_audit',
                from_event_id INTEGER NOT NULL DEFAULT 0,
                to_event_id INTEGER NOT NULL DEFAULT 0,
                event_count INTEGER NOT NULL DEFAULT 0,
                root_hash TEXT NOT NULL DEFAULT '',
                anchor_backend TEXT NOT NULL DEFAULT 'local',
                anchor_status TEXT NOT NULL DEFAULT 'local_only',
                anchor_reference TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        existing_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(herald_items)").fetchall()
        }

        column_additions = {
            "tags": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "feed_url": "TEXT NOT NULL DEFAULT ''",
            "image_url": "TEXT NOT NULL DEFAULT ''",
            "post_attempts_count": "INTEGER NOT NULL DEFAULT 0",
            "last_post_attempt": "TEXT NOT NULL DEFAULT ''",
            "last_post_error": "TEXT NOT NULL DEFAULT ''",
        }

        for column_name, column_def in column_additions.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE herald_items ADD COLUMN {column_name} {column_def}"
                )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_herald_items_source_external_id
            ON herald_items(source, external_id)
            WHERE external_id <> ''
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_herald_items_url
            ON herald_items(url)
            WHERE url <> ''
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_herald_audit_events_item_id
            ON herald_audit_events(item_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_herald_audit_events_event_type
            ON herald_audit_events(event_type)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_herald_audit_events_created_at
            ON herald_audit_events(created_at)
            """
        )

        conn.commit()


def log_event(
    event_type: str,
    guild_id: str = "",
    channel_id: str = "",
    user_id: str = "",
    detail: str = "",
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO herald_events
                (event_type, guild_id, channel_id, user_id, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                guild_id,
                channel_id,
                user_id,
                detail,
            ),
        )
        conn.commit()


def audit_event(
    event_type: str,
    item_id: int | None = None,
    actor: str = "herald",
    old_status: str = "",
    new_status: str = "",
    reason: str = "",
    detail: str = "",
    payload: dict | None = None,
) -> str:
    init_db()

    with connect() as conn:
        event_hash = _insert_audit_event(
            conn,
            event_type=event_type,
            item_id=item_id,
            actor=actor,
            old_status=old_status,
            new_status=new_status,
            reason=reason,
            detail=detail,
            payload=payload,
        )
        conn.commit()
        return event_hash


def count_events() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM herald_events").fetchone()
        return int(row["total"] or 0)


def count_audit_events() -> int:
    init_db()

    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM herald_audit_events"
        ).fetchone()
        return int(row["total"] or 0)


def count_items_by_status(status: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM herald_items WHERE status=?",
            (normalise_status(status),),
        ).fetchone()
        return int(row["total"] or 0)


def count_items_by_category(category: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM herald_items WHERE category=?",
            (category,),
        ).fetchone()
        return int(row["total"] or 0)


def upsert_item(item: dict, status: str = WATCH_STATUS_HELD) -> dict:
    init_db()

    category = (item.get("category") or "").strip()
    source = (item.get("source") or "").strip()
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    summary = (item.get("summary") or "").strip()
    external_id = str(item.get("external_id") or "").strip()
    tags = item.get("tags") or []
    tags_text = ",".join(sorted(set(str(tag).strip() for tag in tags if str(tag).strip())))
    published_at = (item.get("published_at") or "").strip()
    feed_url = (item.get("feed_url") or "").strip()
    image_url = (item.get("image_url") or "").strip()
    status = normalise_status(status)

    if not category or not source or not title or not url:
        return {
            "created": False,
            "id": None,
            "status": "",
            "reason": "missing required field",
        }

    with connect() as conn:
        existing = None

        if source and external_id:
            existing = conn.execute(
                """
                SELECT id, status
                FROM herald_items
                WHERE source=? AND external_id=?
                LIMIT 1
                """,
                (source, external_id),
            ).fetchone()

        if existing is None and url:
            existing = conn.execute(
                """
                SELECT id, status
                FROM herald_items
                WHERE url=?
                LIMIT 1
                """,
                (url,),
            ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE herald_items
                SET
                    category=?,
                    source=?,
                    title=?,
                    url=?,
                    summary=?,
                    external_id=?,
                    tags=?,
                    published_at=?,
                    feed_url=?,
                    image_url=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    category,
                    source,
                    title,
                    url,
                    summary,
                    external_id,
                    tags_text,
                    published_at,
                    feed_url,
                    image_url,
                    int(existing["id"]),
                ),
            )
            conn.commit()

            return {
                "created": False,
                "id": int(existing["id"]),
                "status": existing["status"],
                "reason": "existing",
            }

        cursor = conn.execute(
            """
            INSERT INTO herald_items (
                category,
                source,
                title,
                url,
                summary,
                status,
                external_id,
                tags,
                published_at,
                feed_url,
                image_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                source,
                title,
                url,
                summary,
                status,
                external_id,
                tags_text,
                published_at,
                feed_url,
                image_url,
            ),
        )

        item_id = int(cursor.lastrowid)

        _insert_audit_event(
            conn,
            event_type="item_created",
            item_id=item_id,
            actor="herald",
            old_status="",
            new_status=status,
            reason="discovered",
            detail=f"{source}: {title}",
            payload={
                "category": category,
                "source": source,
                "title": title,
                "url": url,
                "external_id": external_id,
                "tags": tags_text,
                "published_at": published_at,
                "feed_url": feed_url,
            },
        )

        conn.commit()

        return {
            "created": True,
            "id": item_id,
            "status": status,
            "reason": "created",
        }


def list_items_by_status(status: str, limit: int = 10) -> list[dict]:
    init_db()
    status = normalise_status(status)

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM herald_items
            WHERE status=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (status, int(limit)),
        ).fetchall()

    return [dict(row) for row in rows]


def get_item_by_id(item_id: int) -> dict | None:
    init_db()

    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM herald_items
            WHERE id=?
            LIMIT 1
            """,
            (int(item_id),),
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def set_item_status(
    item_id: int,
    status: str,
    reason: str = "status_changed",
    actor: str = "herald",
) -> bool:
    init_db()
    status = normalise_status(status)

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id, status, title, source, category
            FROM herald_items
            WHERE id=?
            LIMIT 1
            """,
            (int(item_id),),
        ).fetchone()

        if existing is None:
            return False

        old_status = str(existing["status"] or "")

        if old_status == status:
            return True

        cursor = conn.execute(
            """
            UPDATE herald_items
            SET status=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, int(item_id)),
        )

        if cursor.rowcount > 0:
            _insert_audit_event(
                conn,
                event_type="status_changed",
                item_id=int(item_id),
                actor=actor,
                old_status=old_status,
                new_status=status,
                reason=reason,
                detail=f"{existing['source']}: {existing['title']}",
                payload={
                    "title": existing["title"],
                    "source": existing["source"],
                    "category": existing["category"],
                },
            )

        conn.commit()
        return cursor.rowcount > 0


def mark_item_posted(item_id: int, discord_message_id: str = "") -> bool:
    init_db()

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id, status, title, source, category
            FROM herald_items
            WHERE id=?
            LIMIT 1
            """,
            (int(item_id),),
        ).fetchone()

        if existing is None:
            return False

        old_status = str(existing["status"] or "")
        message_id = str(discord_message_id or "")

        cursor = conn.execute(
            """
            UPDATE herald_items
            SET
                status=?,
                discord_message_id=?,
                last_post_error='',
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                WATCH_STATUS_POSTED,
                message_id,
                int(item_id),
            ),
        )

        if cursor.rowcount > 0:
            _insert_audit_event(
                conn,
                event_type="posted",
                item_id=int(item_id),
                actor="herald",
                old_status=old_status,
                new_status=WATCH_STATUS_POSTED,
                reason="discord_send_success",
                detail=f"discord_message_id={message_id}",
                payload={
                    "title": existing["title"],
                    "source": existing["source"],
                    "category": existing["category"],
                    "discord_message_id": message_id,
                },
            )

        conn.commit()
        return cursor.rowcount > 0


def mark_item_failed(item_id: int, error: str) -> bool:
    init_db()

    error = (error or "").strip()

    if len(error) > 500:
        error = error[:500].rstrip() + "..."

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id, status, title, source, category
            FROM herald_items
            WHERE id=?
            LIMIT 1
            """,
            (int(item_id),),
        ).fetchone()

        if existing is None:
            return False

        old_status = str(existing["status"] or "")

        cursor = conn.execute(
            """
            UPDATE herald_items
            SET
                status=?,
                post_attempts_count=post_attempts_count + 1,
                last_post_attempt=CURRENT_TIMESTAMP,
                last_post_error=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                WATCH_STATUS_FAILED,
                error,
                int(item_id),
            ),
        )

        if cursor.rowcount > 0:
            _insert_audit_event(
                conn,
                event_type="failed",
                item_id=int(item_id),
                actor="herald",
                old_status=old_status,
                new_status=WATCH_STATUS_FAILED,
                reason="discord_send_failed",
                detail=error,
                payload={
                    "title": existing["title"],
                    "source": existing["source"],
                    "category": existing["category"],
                    "error": error,
                },
            )

        conn.commit()
        return cursor.rowcount > 0


def retry_failed_items() -> int:
    init_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, status, title, source, category
            FROM herald_items
            WHERE status=?
            ORDER BY id ASC
            """,
            (WATCH_STATUS_FAILED,),
        ).fetchall()

        changed = 0

        for row in rows:
            cursor = conn.execute(
                """
                UPDATE herald_items
                SET status=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status=?
                """,
                (
                    WATCH_STATUS_PENDING,
                    int(row["id"]),
                    WATCH_STATUS_FAILED,
                ),
            )

            if cursor.rowcount > 0:
                changed += 1

                _insert_audit_event(
                    conn,
                    event_type="status_changed",
                    item_id=int(row["id"]),
                    actor="herald",
                    old_status=WATCH_STATUS_FAILED,
                    new_status=WATCH_STATUS_PENDING,
                    reason="retry_failed",
                    detail=f"{row['source']}: {row['title']}",
                    payload={
                        "title": row["title"],
                        "source": row["source"],
                        "category": row["category"],
                    },
                )

        conn.commit()
        return changed


def list_audit_events_for_item(item_id: int, limit: int = 25) -> list[dict]:
    init_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM herald_audit_events
            WHERE item_id=?
            ORDER BY id ASC
            LIMIT ?
            """,
            (int(item_id), int(limit)),
        ).fetchall()

    return [dict(row) for row in rows]


def list_recent_audit_events(limit: int = 20) -> list[dict]:
    init_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM herald_audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    return [dict(row) for row in rows]


def verify_audit_chain() -> dict:
    init_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM herald_audit_events
            ORDER BY id ASC
            """
        ).fetchall()

    previous_hash = ""
    checked = 0

    for row in rows:
        checked += 1

        payload_json = str(row["payload_json"] or "")
        expected_payload_hash = _sha256_text(payload_json)

        if expected_payload_hash != str(row["payload_hash"] or ""):
            return {
                "ok": False,
                "checked": checked,
                "first_bad_event_id": int(row["id"]),
                "reason": "payload_hash mismatch",
                "last_event_hash": previous_hash,
            }

        if str(row["previous_event_hash"] or "") != previous_hash:
            return {
                "ok": False,
                "checked": checked,
                "first_bad_event_id": int(row["id"]),
                "reason": "previous_event_hash mismatch",
                "last_event_hash": previous_hash,
            }

        expected_event_hash = _make_audit_event_hash(
            item_id=row["item_id"],
            event_type=str(row["event_type"] or ""),
            actor=str(row["actor"] or ""),
            old_status=str(row["old_status"] or ""),
            new_status=str(row["new_status"] or ""),
            reason=str(row["reason"] or ""),
            detail=str(row["detail"] or ""),
            payload_hash=str(row["payload_hash"] or ""),
            previous_event_hash=str(row["previous_event_hash"] or ""),
            created_at=str(row["created_at"] or ""),
        )

        if expected_event_hash != str(row["event_hash"] or ""):
            return {
                "ok": False,
                "checked": checked,
                "first_bad_event_id": int(row["id"]),
                "reason": "event_hash mismatch",
                "last_event_hash": previous_hash,
            }

        previous_hash = str(row["event_hash"] or "")

    return {
        "ok": True,
        "checked": checked,
        "first_bad_event_id": None,
        "reason": "",
        "last_event_hash": previous_hash,
    }


def audit_item_text(item_id: int) -> str:
    item = get_item_by_id(item_id)

    if item is None:
        return f"🎺 I could not find Herald item `{item_id}`."

    events = list_audit_events_for_item(item_id, 50)
    verification = verify_audit_chain()

    lines = [
        f"🎺 **Herald audit for item `{item_id}`**",
        "",
        "**Current item:**",
        f"Title: **{item.get('title', '')}**",
        f"Status: `{item.get('status', '')}`",
        f"Source: `{item.get('source', '')}`",
        f"Category: `{item.get('category', '')}`",
        "",
        "**Audit trail:**",
    ]

    if not events:
        lines.append("No audit events found for this item yet.")
    else:
        for event in events:
            old_status = event.get("old_status") or ""
            new_status = event.get("new_status") or ""
            transition = ""

            if old_status or new_status:
                transition = f" `{old_status or 'none'}` → `{new_status or 'none'}`"

            reason = event.get("reason") or ""
            detail = event.get("detail") or ""

            lines.append(
                f"{event.get('id')}. `{event.get('created_at')}` — "
                f"**{event.get('event_type')}**{transition}"
            )

            if reason:
                lines.append(f"   reason: `{reason}`")

            if detail:
                lines.append(f"   detail: {detail[:250]}")

    lines.append("")
    lines.append(
        "Chain: `PASS`"
        if verification.get("ok")
        else f"Chain: `FAIL` at event `{verification.get('first_bad_event_id')}` — {verification.get('reason')}"
    )

    return "\n".join(lines)


def audit_verify_text() -> str:
    result = verify_audit_chain()

    lines = [
        "🎺 **Herald audit verification**",
        "",
        f"Events checked: `{result.get('checked', 0)}`",
        f"Chain status: `{'PASS' if result.get('ok') else 'FAIL'}`",
    ]

    if result.get("ok"):
        last_hash = result.get("last_event_hash") or ""
        lines.append(f"Last event hash: `{last_hash[:16]}...`" if last_hash else "Last event hash: `none yet`")
    else:
        lines.append(f"First bad event id: `{result.get('first_bad_event_id')}`")
        lines.append(f"Reason: `{result.get('reason')}`")

    return "\n".join(lines)


def _safe_json_loads(value: str) -> dict:
    try:
        loaded = json.loads(value or "{}")
    except Exception:
        return {}

    if isinstance(loaded, dict):
        return loaded

    return {}


def _short_text(value: str, max_chars: int = 180) -> str:
    value = str(value or "").strip()

    if len(value) > max_chars:
        return value[:max_chars].rstrip() + "..."

    return value


def audit_recent_text(limit: int = 10) -> str:
    init_db()

    try:
        limit = int(limit)
    except Exception:
        limit = 10

    if limit <= 0:
        limit = 10

    if limit > 50:
        limit = 50

    events = list_recent_audit_events(limit)
    verification = verify_audit_chain()

    lines = [
        f"🎺 **Recent Herald audit events**",
        "",
        f"Showing: `{len(events)}`",
        f"Chain: `{'PASS' if verification.get('ok') else 'FAIL'}`",
    ]

    if not verification.get("ok"):
        lines.append(
            f"First bad event: `{verification.get('first_bad_event_id')}` — {verification.get('reason')}"
        )

    lines.append("")

    if not events:
        lines.append("No audit events yet.")
        return "\n".join(lines)

    for event in events:
        payload = _safe_json_loads(event.get("payload_json") or "")

        event_id = event.get("id")
        event_type = event.get("event_type") or ""
        item_id = event.get("item_id")
        actor = event.get("actor") or ""
        reason = event.get("reason") or ""
        old_status = event.get("old_status") or ""
        new_status = event.get("new_status") or ""
        created_at = event.get("created_at") or ""
        detail = event.get("detail") or ""

        title = (
            payload.get("title")
            or payload.get("command")
            or detail
            or "no detail"
        )

        source = payload.get("source") or ""
        category = payload.get("category") or ""

        transition = ""
        if old_status or new_status:
            transition = f" `{old_status or 'none'}` → `{new_status or 'none'}`"

        item_text = f" item `{item_id}`" if item_id else ""

        lines.append(
            f"**#{event_id}** `{event_type}`{item_text}{transition}"
        )

        if title:
            if source:
                lines.append(f"{source}: {_short_text(title, 140)}")
            else:
                lines.append(_short_text(title, 160))

        meta = []

        if category:
            meta.append(f"category: `{category}`")

        if actor:
            meta.append(f"actor: `{actor}`")

        if reason:
            meta.append(f"reason: `{reason}`")

        if created_at:
            meta.append(f"time: `{created_at}`")

        if meta:
            lines.append(" | ".join(meta))

        event_hash = event.get("event_hash") or ""
        if event_hash:
            lines.append(f"hash: `{event_hash[:16]}...`")

        lines.append("")

    return "\n".join(lines).strip()


def audit_summary_text() -> str:
    init_db()

    verification = verify_audit_chain()
    events = list_recent_audit_events(500)

    event_counts = {}
    category_counts = {}
    actor_counts = {}

    latest_event_id = None
    latest_created_at = ""

    for event in events:
        event_type = event.get("event_type") or "unknown"
        actor = event.get("actor") or "unknown"
        payload = _safe_json_loads(event.get("payload_json") or "")
        category = payload.get("category") or ""

        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        actor_counts[actor] = actor_counts.get(actor, 0) + 1

        if category:
            category_counts[category] = category_counts.get(category, 0) + 1

        if latest_event_id is None:
            latest_event_id = event.get("id")
            latest_created_at = event.get("created_at") or ""

    lines = [
        "🎺 **Herald audit summary**",
        "",
        f"Events checked: `{verification.get('checked', 0)}`",
        f"Chain status: `{'PASS' if verification.get('ok') else 'FAIL'}`",
    ]

    if verification.get("ok"):
        last_hash = verification.get("last_event_hash") or ""
        lines.append(
            f"Last event hash: `{last_hash[:16]}...`"
            if last_hash
            else "Last event hash: `none yet`"
        )
    else:
        lines.append(f"First bad event id: `{verification.get('first_bad_event_id')}`")
        lines.append(f"Reason: `{verification.get('reason')}`")

    if latest_event_id is not None:
        lines.append(f"Latest event: `#{latest_event_id}` at `{latest_created_at}`")

    lines.append("")
    lines.append("**By event type:**")

    if event_counts:
        for name, count in sorted(event_counts.items()):
            lines.append(f"- `{name}`: `{count}`")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("**By category:**")

    if category_counts:
        for name, count in sorted(category_counts.items()):
            lines.append(f"- `{name}`: `{count}`")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("**By actor:**")

    if actor_counts:
        for name, count in sorted(actor_counts.items()):
            lines.append(f"- `{name}`: `{count}`")
    else:
        lines.append("- none")

    return "\n".join(lines)    