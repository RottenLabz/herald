import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from config import HERALD_DB_PATH
from presentation import escape_provider_text


WATCH_STATUS_HELD = "held"
WATCH_STATUS_PENDING = "pending"
WATCH_STATUS_POSTED = "posted"
WATCH_STATUS_FAILED = "failed"
WATCH_STATUS_SKIPPED = "skipped"
WATCH_STATUS_SENDING = "sending"
WATCH_STATUS_UNCERTAIN = "uncertain"
SCHEMA_VERSION = 2
FROZEN_STATUSES = {WATCH_STATUS_POSTED, WATCH_STATUS_SENDING, WATCH_STATUS_UNCERTAIN}

VALID_WATCH_STATUSES = {
    WATCH_STATUS_HELD,
    WATCH_STATUS_PENDING,
    WATCH_STATUS_POSTED,
    WATCH_STATUS_FAILED,
    WATCH_STATUS_SKIPPED,
    WATCH_STATUS_SENDING,
    WATCH_STATUS_UNCERTAIN,
}


def _db_path() -> Path:
    path = Path(HERALD_DB_PATH).expanduser()

    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")

    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _begin_write(conn: sqlite3.Connection) -> None:
    # Acquire SQLite's write reservation before reading the previous audit hash.
    # Without this, concurrent watcher/owner writes can both inherit the same
    # previous hash and create a forked audit chain even though every INSERT succeeds.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


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
    """Transactional, idempotent schema migration; never recover active claims here."""
    with connect() as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this Herald build")
        if version == SCHEMA_VERSION:
            return
        _begin_write(conn)
        # Another process may have completed migration while this one waited.
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError("Database schema is newer than this Herald build")
        if version == SCHEMA_VERSION:
            return
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
            "provider_id": "TEXT NOT NULL DEFAULT ''",
            "source_id": "TEXT NOT NULL DEFAULT ''",
            "source_policy_digest": "TEXT NOT NULL DEFAULT ''",
            "delivery_mode": "TEXT NOT NULL DEFAULT 'review'",
            "destination_channel_id": "TEXT NOT NULL DEFAULT ''",
            "subscription_role_id": "TEXT NOT NULL DEFAULT ''",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "attribution_label": "TEXT NOT NULL DEFAULT ''",
            "attribution_url": "TEXT NOT NULL DEFAULT ''",
            "private": "INTEGER NOT NULL DEFAULT 0",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "content_digest": "TEXT NOT NULL DEFAULT ''",
            "approval_revision": "INTEGER",
            "claim_token": "TEXT NOT NULL DEFAULT ''",
            "claimed_at": "TEXT NOT NULL DEFAULT ''",
            "claimed_revision": "INTEGER",
        }

        for column_name, column_def in column_additions.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE herald_items ADD COLUMN {column_name} {column_def}"
                )

        # Replace the old cross-category identity constraint, without deleting rows.
        conn.execute("DROP INDEX IF EXISTS idx_herald_items_source_external_id")
        for row in conn.execute("SELECT * FROM herald_items ORDER BY id").fetchall():
            material = dict(row)
            material["source_id"] = material["source_id"] or material["source"]
            digest = material_digest(material)
            old_status = material["status"]
            new_status = WATCH_STATUS_HELD if old_status == WATCH_STATUS_PENDING else old_status
            conn.execute(
                "UPDATE herald_items SET source_id=?, content_digest=?, status=?, approval_revision=NULL WHERE id=?",
                (material["source_id"], digest, new_status, row["id"]),
            )
            _insert_audit_event(
                conn, event_type="schema_migration", item_id=row["id"],
                actor="migration", old_status=old_status, new_status=new_status,
                reason="legacy_revision_baseline",
                payload={"schema_version": SCHEMA_VERSION, "revision": 1,
                         "content_digest": digest, "requires_reapproval": old_status == WATCH_STATUS_PENDING},
            )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_herald_items_scoped_external_id
            ON herald_items(provider_id, source_id, category, external_id) WHERE external_id <> ''"""
        )

        # URL is useful as a lookup fallback for article/feed items, but it is
        # not globally unique. Twitch channels reuse the same permanent channel
        # URL for every new live broadcast, while the Twitch stream ID changes.
        conn.execute("DROP INDEX IF EXISTS idx_herald_items_url")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_herald_items_url_lookup
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

        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
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
        _begin_write(conn)
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


# Every field used to render or route a message is bound to its approval.
MATERIAL_FIELDS = (
    "provider_id", "source_id", "source_policy_digest", "source", "category", "external_id", "title", "url",
    "summary", "tags", "published_at", "feed_url", "image_url", "delivery_mode",
    "destination_channel_id", "subscription_role_id", "source_url",
    "attribution_label", "attribution_url", "private",
)


def material_digest(item: dict) -> str:
    return _sha256_text(_json_dumps({key: item.get(key, "") for key in MATERIAL_FIELDS}))


def _item_dict(row) -> dict:
    item = dict(row)
    # Provider/public API aliases; canonical DB fields remain explicit.
    item["channel_id"] = item["destination_channel_id"]
    item["role_id"] = item["subscription_role_id"]
    return item


def schema_version() -> int:
    init_db()
    with connect() as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _normalise_material(item: dict, status: str) -> dict:
    material = {key: str(item.get(key) or "").strip() for key in MATERIAL_FIELDS}
    material["source_id"] = material["source_id"] or material["source"]
    material["delivery_mode"] = material["delivery_mode"] or (
        "automatic" if status == WATCH_STATUS_PENDING else "review"
    )
    if material["delivery_mode"] not in {"automatic", "review"}:
        raise ValueError("delivery_mode must be automatic or review")
    material["destination_channel_id"] = str(item.get("destination_channel_id") or item.get("channel_id") or "")
    material["subscription_role_id"] = str(item.get("subscription_role_id") or item.get("role_id") or "")
    material["private"] = int(bool(item.get("private", False)))
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    material["tags"] = ",".join(sorted({str(tag).strip() for tag in tags if str(tag).strip()}))
    return material


def upsert_item(item: dict, status: str | None = None) -> dict:
    init_db()
    status = normalise_status(status if status is not None else (
        WATCH_STATUS_PENDING if item.get("delivery_mode") == "automatic" else WATCH_STATUS_HELD
    ))
    material = _normalise_material(item, status)
    if not all(material[key] for key in ("category", "source", "title", "url")):
        return {"created": False, "id": None, "status": "", "reason": "missing required field"}
    digest = material_digest(material)
    with connect() as conn:
        _begin_write(conn)
        existing = None
        if material["external_id"]:
            existing = conn.execute(
                "SELECT * FROM herald_items WHERE provider_id=? AND source_id=? AND category=? AND external_id=? LIMIT 1",
                (material["provider_id"], material["source_id"], material["category"], material["external_id"]),
            ).fetchone()
        # A source's distinct external IDs are authoritative (e.g. recurring broadcasts).
        # URL fallback is restricted to the same provider/source/category and a missing ID.
        if existing is None and item.get("dedupe_by_url", True):
            existing = conn.execute(
                """SELECT * FROM herald_items WHERE provider_id=? AND source_id=? AND category=? AND url=?
                AND (external_id='' OR ?='') ORDER BY id LIMIT 1""",
                (material["provider_id"], material["source_id"], material["category"], material["url"], material["external_id"]),
            ).fetchone()
        if existing is not None:
            item_id = int(existing["id"])
            old_status = existing["status"]
            result = {"created": False, "id": item_id, "status": old_status,
                      "revision": existing["revision"], "reason": "existing"}
            if old_status in FROZEN_STATUSES:
                result["reason"] = "frozen" if existing["content_digest"] != digest else "existing"
                return result
            if existing["content_digest"] == digest:
                return result
            revision = int(existing["revision"]) + 1
            if old_status == WATCH_STATUS_SKIPPED:
                new_status = WATCH_STATUS_SKIPPED
            else:
                new_status = WATCH_STATUS_PENDING if material["delivery_mode"] == "automatic" else WATCH_STATUS_HELD
            approval = revision if material["delivery_mode"] == "automatic" and new_status == WATCH_STATUS_PENDING else None
            assignments = ", ".join(f"{key}=?" for key in MATERIAL_FIELDS)
            conn.execute(
                f"""UPDATE herald_items SET {assignments}, revision=?, content_digest=?,
                approval_revision=?, status=?, post_attempts_count=0,
                last_post_attempt='', last_post_error='', discord_message_id='',
                claim_token='', claimed_at='', claimed_revision=NULL,
                updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND revision=? AND status=?""",
                tuple(material[key] for key in MATERIAL_FIELDS) +
                (revision, digest, approval, new_status, item_id, existing["revision"], old_status),
            )
            _insert_audit_event(
                conn, event_type="item_revised", item_id=item_id,
                old_status=old_status, new_status=new_status, reason="material_content_changed",
                payload={"previous_revision": existing["revision"], "revision": revision,
                         "previous_digest": existing["content_digest"], "content_digest": digest,
                         "approval_revision": approval, "material": material},
            )
            conn.commit()
            return {"created": False, "id": item_id, "status": new_status,
                    "revision": revision, "reason": "revised"}
        # An explicit startup/backlog hold or skip overrides automatic admission.
        # Discovery never fabricates historical/in-flight states or owner approval.
        if status == WATCH_STATUS_SKIPPED:
            status = WATCH_STATUS_SKIPPED
        elif material["delivery_mode"] != "automatic" or status != WATCH_STATUS_PENDING:
            status = WATCH_STATUS_HELD
        approval = 1 if status == WATCH_STATUS_PENDING else None
        columns = ", ".join(MATERIAL_FIELDS)
        placeholders = ", ".join("?" for _ in MATERIAL_FIELDS)
        cursor = conn.execute(
            f"INSERT INTO herald_items ({columns}, status, revision, content_digest, approval_revision) "
            f"VALUES ({placeholders}, ?, 1, ?, ?)",
            tuple(material[key] for key in MATERIAL_FIELDS) + (status, digest, approval),
        )
        item_id = int(cursor.lastrowid)
        _insert_audit_event(
            conn, event_type="item_created", item_id=item_id, new_status=status,
            reason="discovered", payload={**material, "revision": 1, "content_digest": digest,
                                           "approval_revision": approval},
        )
        conn.commit()
        return {"created": True, "id": item_id, "status": status, "revision": 1, "reason": "created"}


def list_items_by_status(
    status: str,
    limit: int = 10,
    *,
    newest_first: bool = True,
) -> list[dict]:
    init_db()
    status = normalise_status(status)
    query = (
        """
        SELECT *
        FROM herald_items
        WHERE status=?
        ORDER BY id DESC
        LIMIT ?
        """
        if newest_first
        else
        """
        SELECT *
        FROM herald_items
        WHERE status=?
        ORDER BY id ASC
        LIMIT ?
        """
    )

    with connect() as conn:
        rows = conn.execute(
            query,
            (status, int(limit)),
        ).fetchall()

    return [_item_dict(row) for row in rows]


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

    return _item_dict(row)


def set_item_status(
    item_id: int, status: str, reason: str = "status_changed", actor: str = "herald",
    *, expected_revision: int | None = None,
) -> bool:
    """Owner/control transition. Delivery finalization must supply a durable claim."""
    init_db()
    if status not in {WATCH_STATUS_HELD, WATCH_STATUS_PENDING, WATCH_STATUS_SKIPPED}:
        return False
    with connect() as conn:
        _begin_write(conn)
        row = conn.execute("SELECT * FROM herald_items WHERE id=?", (int(item_id),)).fetchone()
        if row is None or row["status"] in FROZEN_STATUSES:
            return False
        if expected_revision is not None and int(row["revision"]) != int(expected_revision):
            return False
        if status == WATCH_STATUS_PENDING and actor != "owner" and row["delivery_mode"] != "automatic":
            return False
        approval = row["revision"] if status == WATCH_STATUS_PENDING else None
        if row["status"] == status and row["approval_revision"] == approval:
            return True
        cursor = conn.execute(
            """UPDATE herald_items SET status=?, approval_revision=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status=? AND revision=?""",
            (status, approval, int(item_id), row["status"], row["revision"]),
        )
        if cursor.rowcount:
            _insert_audit_event(
                conn, event_type="status_changed", item_id=int(item_id), actor=actor,
                old_status=row["status"], new_status=status, reason=reason,
                payload={"revision": row["revision"], "content_digest": row["content_digest"],
                         "approval_revision": approval},
            )
        conn.commit()
        return bool(cursor.rowcount)


def approve_item(item_id: int, *, expected_revision: int | None = None, actor: str = "owner") -> bool:
    return set_item_status(item_id, WATCH_STATUS_PENDING, "owner_approved_revision", actor,
                           expected_revision=expected_revision)


def claim_item(
    item_id: int, *, approve: bool = False, expected_revision: int | None = None,
    actor: str = "herald", expected_status: str | None = None,
) -> dict | None:
    """Claim/reload immediately before preparation. No await occurs inside this transaction."""
    init_db()
    with connect() as conn:
        _begin_write(conn)
        row = conn.execute("SELECT * FROM herald_items WHERE id=?", (int(item_id),)).fetchone()
        if row is None or row["status"] not in {WATCH_STATUS_HELD, WATCH_STATUS_PENDING}:
            return None
        if expected_status is not None and row["status"] != expected_status:
            return None
        if expected_revision is not None and row["revision"] != int(expected_revision):
            return None
        if material_digest(dict(row)) != row["content_digest"]:
            return None
        approval = row["approval_revision"]
        if approve and actor == "owner":
            approval = row["revision"]
        if approval != row["revision"] or (row["status"] == WATCH_STATUS_HELD and not approve):
            return None
        token = uuid.uuid4().hex
        cursor = conn.execute(
            """UPDATE herald_items SET status='sending', approval_revision=?, claim_token=?,
            claimed_at=?, claimed_revision=revision, post_attempts_count=MIN(post_attempts_count+1, 2147483647),
            last_post_attempt=CURRENT_TIMESTAMP, last_post_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status=? AND revision=?""",
            (approval, token, _utc_now_text(), int(item_id), row["status"], row["revision"]),
        )
        if not cursor.rowcount:
            return None
        _insert_audit_event(
            conn, event_type="delivery_claimed", item_id=int(item_id), actor=actor,
            old_status=row["status"], new_status=WATCH_STATUS_SENDING,
            reason="owner_approved_and_claimed" if approve else "authorised_revision_claimed",
            payload={"claim_token": token, "revision": row["revision"],
                     "approval_revision": approval, "content_digest": row["content_digest"]},
        )
        claimed = conn.execute("SELECT * FROM herald_items WHERE id=?", (int(item_id),)).fetchone()
        conn.commit()
        return _item_dict(claimed)


def _finish_claim(item_id: int, token: str, revision: int, status: str, error: str = "", message_id: str = "") -> bool:
    init_db()
    error = str(error or "").strip()[:500]
    message_id = str(message_id or "").strip()[:100]
    with connect() as conn:
        _begin_write(conn)
        row = conn.execute("SELECT * FROM herald_items WHERE id=?", (int(item_id),)).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            """UPDATE herald_items SET status=?, discord_message_id=?, last_post_error=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='sending' AND claim_token=?
            AND revision=? AND claimed_revision=? AND approval_revision=?""",
            (status, message_id, error, int(item_id), token, int(revision), int(revision), int(revision)),
        )
        if cursor.rowcount:
            _insert_audit_event(
                conn, event_type=status, item_id=int(item_id),
                old_status=WATCH_STATUS_SENDING, new_status=status,
                reason={WATCH_STATUS_POSTED: "discord_send_success", WATCH_STATUS_FAILED: "safe_pre_send_failure",
                        WATCH_STATUS_UNCERTAIN: "delivery_acceptance_unknown"}[status],
                detail=error, payload={"claim_token": token, "revision": int(revision),
                                      "content_digest": row["content_digest"], "discord_message_id": message_id},
            )
        conn.commit()
        return bool(cursor.rowcount)


def mark_item_posted(item_id: int, discord_message_id: str = "", *, claim_token: str = "", revision: int | None = None) -> bool:
    if not claim_token or revision is None or not str(discord_message_id).strip():
        return False
    return _finish_claim(item_id, claim_token, revision, WATCH_STATUS_POSTED, message_id=discord_message_id)


def mark_item_uncertain(item_id: int, error: str, *, claim_token: str, revision: int) -> bool:
    return _finish_claim(item_id, claim_token, revision, WATCH_STATUS_UNCERTAIN, error=error)


def mark_item_failed(item_id: int, error: str, *, claim_token: str = "", revision: int | None = None) -> bool:
    """Safe preparation failures only. Errors after entering send are uncertain."""
    if claim_token and revision is not None:
        return _finish_claim(item_id, claim_token, revision, WATCH_STATUS_FAILED, error=error)
    # Retain legacy preparation-failure accounting, while rejecting in-flight/final rows.
    init_db()
    with connect() as conn:
        _begin_write(conn)
        row = conn.execute("SELECT * FROM herald_items WHERE id=?", (int(item_id),)).fetchone()
        if row is None or row["status"] != WATCH_STATUS_PENDING:
            return False
        bounded_error = str(error or "")[:500]
        cursor = conn.execute(
            """UPDATE herald_items SET status='failed', post_attempts_count=MIN(post_attempts_count+1, 2147483647),
            last_post_attempt=CURRENT_TIMESTAMP, last_post_error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='pending' AND revision=?""", (bounded_error, int(item_id), row["revision"]),
        )
        if cursor.rowcount:
            _insert_audit_event(conn, event_type="failed", item_id=int(item_id), old_status=WATCH_STATUS_PENDING,
                                new_status=WATCH_STATUS_FAILED, reason="safe_pre_send_failure", detail=bounded_error,
                                payload={"revision": row["revision"]})
        conn.commit()
        return bool(cursor.rowcount)


def retry_failed_items(max_attempts: int | None = None) -> int:
    init_db()
    with connect() as conn:
        _begin_write(conn)
        rows = conn.execute(
            """SELECT * FROM herald_items WHERE status='failed' AND approval_revision=revision
            AND (? IS NULL OR post_attempts_count < ?) ORDER BY id LIMIT 1000""",
            (max_attempts, max_attempts),
        ).fetchall()
        changed = 0
        for row in rows:
            cursor = conn.execute(
                "UPDATE herald_items SET status='pending', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='failed' AND revision=?",
                (row["id"], row["revision"]),
            )
            if cursor.rowcount:
                changed += 1
                _insert_audit_event(conn, event_type="status_changed", item_id=row["id"],
                                    old_status=WATCH_STATUS_FAILED, new_status=WATCH_STATUS_PENDING,
                                    reason="retry_safe_failure", payload={"revision": row["revision"]})
        conn.commit()
        return changed


def recover_interrupted_claims() -> int:
    """Call once during process startup, before scheduling any delivery work.

    SQLite cannot tell whether Discord accepted a send before a crash. Never retry here.
    Repeated init_db/read calls deliberately do not call this function.
    """
    init_db()
    with connect() as conn:
        _begin_write(conn)
        rows = conn.execute("SELECT * FROM herald_items WHERE status='sending' ORDER BY id").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE herald_items SET status='uncertain', last_post_error='Interrupted delivery; owner reconciliation required', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='sending' AND claim_token=?",
                (row["id"], row["claim_token"]),
            )
            _insert_audit_event(conn, event_type="uncertain", item_id=row["id"],
                                old_status=WATCH_STATUS_SENDING, new_status=WATCH_STATUS_UNCERTAIN,
                                reason="startup_interrupted_claim", payload={"claim_token": row["claim_token"], "revision": row["revision"]})
        conn.commit()
        return len(rows)


def valid_uncertainty_resolution(resolution: str, message_id: str = "") -> bool:
    """Shared transport boundary: exact action and an ASCII Discord receipt ID."""
    if not isinstance(resolution, str) or resolution not in {"posted", "retry", "skip"}:
        return False
    if not isinstance(message_id, str):
        return False
    return resolution != "posted" or (
        1 <= len(message_id) <= 20 and message_id.isascii() and message_id.isdigit()
    )


def resolve_uncertain(
    item_id: int, resolution: str, *, message_id: str = "", actor: str = "owner",
    expected_revision: int | None = None,
) -> bool:
    """Deliberate owner reconciliation: posted receipt, confirmed-safe retry, or skip.

    'retry' asserts the operator has checked Discord and accepts the duplicate risk.
    """
    if actor != "owner" or not valid_uncertainty_resolution(resolution, message_id):
        return False
    init_db()
    with connect() as conn:
        _begin_write(conn)
        row = conn.execute("SELECT * FROM herald_items WHERE id=?", (int(item_id),)).fetchone()
        if row is None or row["status"] != WATCH_STATUS_UNCERTAIN:
            return False
        if expected_revision is not None and row["revision"] != int(expected_revision):
            return False
        new_status = {"posted": WATCH_STATUS_POSTED, "retry": WATCH_STATUS_PENDING, "skip": WATCH_STATUS_SKIPPED}[resolution]
        approval = row["revision"] if resolution in {"posted", "retry"} else None
        cursor = conn.execute(
            """UPDATE herald_items SET status=?, approval_revision=?, discord_message_id=?, claim_token='',
            claimed_at='', claimed_revision=NULL, last_post_error='', updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='uncertain' AND revision=? AND claim_token=?""",
            (new_status, approval, str(message_id)[:100] if resolution == "posted" else "", int(item_id), row["revision"], row["claim_token"]),
        )
        if cursor.rowcount:
            _insert_audit_event(conn, event_type="uncertainty_resolved", item_id=int(item_id), actor=actor,
                                old_status=WATCH_STATUS_UNCERTAIN, new_status=new_status,
                                reason="owner_reconciled_" + resolution,
                                payload={"revision": row["revision"], "prior_claim_token": row["claim_token"],
                                         "discord_message_id": str(message_id)[:100], "duplicate_risk_acknowledged": resolution == "retry"})
        conn.commit()
        return bool(cursor.rowcount)


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


def list_all_audit_events() -> list[dict]:
    init_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM herald_audit_events
            ORDER BY id DESC
            """
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
        f"Title: **{escape_provider_text(item.get('title', ''), 500)}**",
        f"Status: {escape_provider_text(item.get('status', ''), 80)}",
        f"Source: {escape_provider_text(item.get('source', ''), 200)}",
        f"Category: {escape_provider_text(item.get('category', ''), 100)}",
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
                transition = f" {escape_provider_text(old_status or 'none', 80)} → {escape_provider_text(new_status or 'none', 80)}"

            reason = event.get("reason") or ""
            detail = event.get("detail") or ""

            lines.append(
                f"{event.get('id')}. {escape_provider_text(event.get('created_at'), 80)} — "
                f"**{escape_provider_text(event.get('event_type'), 100)}**{transition}"
            )

            if reason:
                lines.append(f"   reason: {escape_provider_text(reason, 250)}")

            if detail:
                lines.append(f"   detail: {escape_provider_text(detail, 250)}")

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
        "🎺 **Recent Herald audit events**",
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
            transition = f" {escape_provider_text(old_status or 'none', 80)} → {escape_provider_text(new_status or 'none', 80)}"

        item_text = f" item `{item_id}`" if item_id else ""

        lines.append(
            f"**#{event_id}** {escape_provider_text(event_type, 100)}{item_text}{transition}"
        )

        if title:
            if source:
                lines.append(f"{escape_provider_text(source, 200)}: {escape_provider_text(title, 140)}")
            else:
                lines.append(escape_provider_text(title, 160))

        meta = []

        if category:
            meta.append(f"category: {escape_provider_text(category, 100)}")

        if actor:
            meta.append(f"actor: {escape_provider_text(actor, 100)}")

        if reason:
            meta.append(f"reason: {escape_provider_text(reason, 250)}")

        if created_at:
            meta.append(f"time: {escape_provider_text(created_at, 80)}")

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
    events = list_all_audit_events()

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
        lines.append(f"Latest event: `#{latest_event_id}` at {escape_provider_text(latest_created_at, 80)}")

    lines.append("")
    lines.append("**By event type:**")

    if event_counts:
        for name, count in sorted(event_counts.items()):
            lines.append(f"- {escape_provider_text(name, 100)}: `{count}`")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("**By category:**")

    if category_counts:
        for name, count in sorted(category_counts.items()):
            lines.append(f"- {escape_provider_text(name, 100)}: `{count}`")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("**By actor:**")

    if actor_counts:
        for name, count in sorted(actor_counts.items()):
            lines.append(f"- {escape_provider_text(name, 100)}: `{count}`")
    else:
        lines.append("- none")

    return "\n".join(lines)
