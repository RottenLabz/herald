import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
import provider_runtime
from providers.common import normalize_item


class TwitchStorageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "herald.db"
        storage.HERALD_DB_PATH = str(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def stream_item(stream_id: str) -> dict:
        return {
            "category": "stream_alerts",
            "source": "Twitch Helix",
            "title": "Example is live",
            "url": "https://www.twitch.tv/example",
            "summary": "",
            "external_id": stream_id,
            "tags": ["twitch", "live"],
            "published_at": "2026-07-14T12:00:00Z",
            "feed_url": "https://api.twitch.tv/helix/streams",
            "image_url": "",
            "dedupe_by_url": False,
            "delivery_mode": "automatic",
        }

    def test_new_stream_id_creates_new_row_for_same_channel_url(self):
        first = storage.upsert_item(self.stream_item("stream-001"), status="pending")
        self.assertTrue(first["created"])
        claim = storage.claim_item(first["id"])
        self.assertIsNotNone(claim)
        self.assertTrue(storage.mark_item_posted(first["id"], "discord-message-1", claim_token=claim["claim_token"], revision=claim["revision"]))

        second = storage.upsert_item(self.stream_item("stream-002"), status="pending")
        self.assertTrue(second["created"])
        self.assertEqual(second["status"], "pending")

        with storage.connect() as conn:
            rows = conn.execute(
                "SELECT status, external_id, url FROM herald_items ORDER BY id"
            ).fetchall()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["external_id"], "stream-001")
        self.assertEqual(rows[0]["status"], "posted")
        self.assertEqual(rows[1]["external_id"], "stream-002")
        self.assertEqual(rows[1]["status"], "pending")
        self.assertEqual(rows[0]["url"], rows[1]["url"])

    def test_same_stream_id_is_still_deduplicated(self):
        first = storage.upsert_item(self.stream_item("stream-001"), status="pending")
        second = storage.upsert_item(self.stream_item("stream-001"), status="pending")

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["id"], first["id"])

    def test_upgrade_drops_legacy_unique_url_index_without_rewriting_history(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE herald_items (
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
                INSERT INTO herald_items (
                    category, source, title, url, status, external_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "stream_alerts",
                    "Twitch Helix",
                    "Example is live",
                    "https://www.twitch.tv/example",
                    "posted",
                    "current-stream-id",
                ),
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX idx_herald_items_url
                ON herald_items(url)
                WHERE url <> ''
                """
            )
            conn.commit()

        storage.init_db()

        with storage.connect() as conn:
            row = conn.execute(
                "SELECT external_id FROM herald_items WHERE id=1"
            ).fetchone()
            old_index = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_herald_items_url'"
            ).fetchone()

        self.assertEqual(row["external_id"], "current-stream-id")
        self.assertIsNone(old_index)

        next_stream = storage.upsert_item(
            self.stream_item("next-stream-id"),
            status="pending",
        )
        self.assertTrue(next_stream["created"])


class SyntheticPrivateProviderTests(unittest.TestCase):
    """Replacement public extension contract, without a removed service implementation.

    OAuth refresh and service-specific request batching belong to the operator's
    future private provider acceptance suite. Core public coverage now verifies
    identity, bounded results and visible failure on the narrow generic interface.
    """
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = {
            "id": "synthetic-live", "source_id": "synthetic-live", "provider_id": "local",
            "name": "Synthetic local source", "url": "https://example.com/feed",
            "enabled": True, "channel_id": 123, "role_id": None,
            "delivery_mode": "automatic", "category": "stream_alerts", "private": True,
            "_plugin": {"path": str(self.root), "module": "synthetic"},
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_plugin(self, body):
        (self.root / "synthetic.py").write_text(body)

    def test_private_provider_retains_distinct_explicit_event_ids(self):
        self.write_plugin("def fetch_items():\n    return [{'title':'Event', 'url':'https://example.com/live', 'external_id':str(n)} for n in range(2)]\n")
        items = provider_runtime.fetch_source(self.source)["items"]
        self.assertEqual([i["external_id"] for i in items], ["0", "1"])
        self.assertEqual(items[0]["url"], items[1]["url"])
        self.assertEqual(items[0]["source_id"], "synthetic-live")
        self.assertEqual(items[0]["category"], "stream_alerts")

    def test_private_provider_exception_is_failed_and_sanitized(self):
        self.write_plugin("def fetch_items():\n    raise RuntimeError('private token must not appear')\n")
        result = provider_runtime.fetch_source(self.source)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["health"]["status"], "failed")
        self.assertNotIn("private token", result["health"]["error"])

    def test_private_provider_results_are_bounded_and_partial_is_visible(self):
        self.write_plugin("def fetch_items():\n    return [{'title':'Event', 'url':'https://example.com/live', 'external_id':str(n)} for n in range(101)]\n")
        result = provider_runtime.fetch_source(self.source)
        self.assertEqual(len(result["items"]), 50)
        self.assertEqual(result["health"]["status"], "degraded")
        self.assertEqual(result["health"]["error"], "entry_limit_reached")


if __name__ == "__main__":
    unittest.main()
