import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
from providers import twitch


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
        }

    def test_new_stream_id_creates_new_row_for_same_channel_url(self):
        first = storage.upsert_item(self.stream_item("stream-001"), status="pending")
        self.assertTrue(first["created"])
        storage.mark_item_posted(first["id"], "discord-message-1")

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


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TwitchProviderTests(unittest.TestCase):
    def setUp(self):
        twitch.TWITCH_ENABLED = True
        twitch.TWITCH_CLIENT_ID = "client-id"
        twitch.TWITCH_CLIENT_SECRET = "client-secret"
        twitch.TWITCH_CHANNELS = ["example"]
        twitch._clear_token_cache()

    def test_provider_marks_channel_url_as_non_identity(self):
        twitch._TOKEN_CACHE["access_token"] = "token"
        twitch._TOKEN_CACHE["expires_at"] = twitch._now() + 3600

        payload = {
            "data": [
                {
                    "id": "stream-001",
                    "user_login": "example",
                    "user_name": "Example",
                    "title": "Live now",
                    "game_name": "Test Game",
                    "started_at": "2026-07-14T12:00:00Z",
                    "thumbnail_url": "https://img/{width}x{height}.jpg",
                    "viewer_count": 10,
                    "language": "en",
                }
            ]
        }

        with patch.object(twitch.requests, "get", return_value=FakeResponse(200, payload)):
            items = twitch.fetch_items()

        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["dedupe_by_url"])
        self.assertEqual(items[0]["external_id"], "stream-001")

    def test_401_obtains_new_app_token_and_retries_once(self):
        twitch._TOKEN_CACHE["access_token"] = "old-token"
        twitch._TOKEN_CACHE["expires_at"] = twitch._now() + 3600

        token_response = FakeResponse(
            200,
            {"access_token": "new-token", "expires_in": 3600},
        )
        get_responses = [
            FakeResponse(401, {"status": 401}),
            FakeResponse(200, {"data": []}),
        ]

        with patch.object(twitch.requests, "post", return_value=token_response) as post_mock, patch.object(
            twitch.requests,
            "get",
            side_effect=get_responses,
        ) as get_mock:
            items = twitch.fetch_items()

        self.assertEqual(items, [])
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(get_mock.call_count, 2)
        self.assertEqual(twitch._TOKEN_CACHE["access_token"], "new-token")

    def test_more_than_100_channels_are_batched(self):
        twitch.TWITCH_CHANNELS = [f"channel{n}" for n in range(101)]
        twitch._TOKEN_CACHE["access_token"] = "token"
        twitch._TOKEN_CACHE["expires_at"] = twitch._now() + 3600

        with patch.object(
            twitch.requests,
            "get",
            side_effect=[FakeResponse(200, {"data": []}), FakeResponse(200, {"data": []})],
        ) as get_mock:
            items = twitch.fetch_items()

        self.assertEqual(items, [])
        self.assertEqual(get_mock.call_count, 2)

        first_params = get_mock.call_args_list[0].kwargs["params"]
        second_params = get_mock.call_args_list[1].kwargs["params"]
        self.assertEqual(sum(1 for key, _ in first_params if key == "user_login"), 100)
        self.assertEqual(sum(1 for key, _ in second_params if key == "user_login"), 1)
        self.assertIn(("first", "100"), first_params)


if __name__ == "__main__":
    unittest.main()
