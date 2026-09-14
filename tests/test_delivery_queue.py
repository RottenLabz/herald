import tempfile
import unittest
from pathlib import Path

import storage
import watchers


class DeliveryQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "herald.db"
        self.old_path = storage.HERALD_DB_PATH
        storage.HERALD_DB_PATH = str(self.db_path)

    def tearDown(self):
        storage.HERALD_DB_PATH = self.old_path
        self.temp_dir.cleanup()

    @staticmethod
    def item(external_id: str) -> dict:
        return {
            "category": "free_games",
            "source": "Test Provider",
            "title": f"Item {external_id}",
            "url": f"https://example.invalid/{external_id}",
            "summary": "",
            "external_id": external_id,
            "tags": [],
            "published_at": "",
            "feed_url": "https://example.invalid/feed",
            "image_url": "",
        }

    def test_skip_inflight_reports_state_instead_of_missing(self):
        item_id = storage.upsert_item(self.item("001"), status="pending")["id"]
        storage.claim_item(item_id)
        stats = watchers.skip_items([item_id])
        self.assertEqual(stats["not_found"], [])
        self.assertEqual(stats["blocked"], [{"id": item_id, "status": "sending"}])
        self.assertIn("sending", watchers.format_skip_stats(stats))

    def test_pending_delivery_is_fifo(self):
        first = storage.upsert_item(self.item("001"), status="pending")
        second = storage.upsert_item(self.item("002"), status="pending")

        pending = storage.list_items_by_status(
            "pending",
            2,
            newest_first=False,
        )

        self.assertEqual(
            [row["id"] for row in pending],
            [first["id"], second["id"]],
        )

    def test_failed_items_stop_requeueing_at_attempt_limit(self):
        created = storage.upsert_item(self.item("001"), status="pending")
        item_id = created["id"]

        claim = storage.claim_item(item_id)
        storage.mark_item_failed(item_id, "first failure", claim_token=claim["claim_token"], revision=claim["revision"])
        self.assertEqual(storage.retry_failed_items(max_attempts=2), 1)

        claim = storage.claim_item(item_id)
        storage.mark_item_failed(item_id, "second failure", claim_token=claim["claim_token"], revision=claim["revision"])
        self.assertEqual(storage.retry_failed_items(max_attempts=2), 0)

        row = storage.get_item_by_id(item_id)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["post_attempts_count"], 2)

        # The owner command is a deliberate manual override of the auto cap.
        self.assertEqual(watchers.retry_failed(), 1)
        row = storage.get_item_by_id(item_id)
        self.assertEqual(row["status"], "pending")


if __name__ == "__main__":
    unittest.main()
