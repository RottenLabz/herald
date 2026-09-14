import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
from delivery import DeliveryCoordinator


class DeliveryIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = storage.HERALD_DB_PATH
        storage.HERALD_DB_PATH = str(Path(self.temp.name) / "delivery.db")

    def tearDown(self):
        storage.HERALD_DB_PATH = self.old_path
        self.temp.cleanup()

    def create(self, identity="one", mode="automatic", **changes):
        return storage.upsert_item({
            "source": "Fixture", "source_id": "fixture", "category": "test",
            "title": identity, "url": "https://example.com/" + identity,
            "external_id": identity, "delivery_mode": mode, **changes,
        })["id"]

    async def test_manual_and_automatic_concurrent_paths_send_once(self):
        item_id = self.create()
        entered = asyncio.Event()
        release = asyncio.Event()
        sent = []

        async def send(row, payload):
            sent.append(row["id"])
            entered.set()
            await release.wait()
            return "123"

        first = DeliveryCoordinator(lambda row: row["title"], send)
        second = DeliveryCoordinator(lambda row: row["title"], send)
        automatic = asyncio.create_task(first.deliver_one(item_id))
        await entered.wait()
        manual = await second.deliver_one(item_id, approve=True, actor="owner")
        self.assertEqual(manual["current_status"], "sending")
        self.assertFalse(storage.set_item_status(item_id, "skipped", actor="owner"))
        release.set()
        result = await automatic
        self.assertEqual(result["status"], "posted")
        self.assertEqual(sent, [item_id])

    async def test_skip_item_waiting_behind_blocked_send_prevents_later_send(self):
        first_id, second_id = self.create("first"), self.create("second")
        entered, release = asyncio.Event(), asyncio.Event()
        sent = []

        async def send(row, payload):
            sent.append(row["id"])
            if row["id"] == first_id:
                entered.set()
                await release.wait()
            return str(row["id"] + 100)

        task = asyncio.create_task(DeliveryCoordinator(lambda row: row["title"], send).deliver_batch(2))
        await entered.wait()
        self.assertTrue(storage.set_item_status(second_id, "skipped", actor="owner"))
        release.set()
        stats = await task
        self.assertEqual(sent, [first_id])
        self.assertEqual((stats["posted"], stats["blocked"]), (1, 1))
        self.assertEqual(storage.get_item_by_id(second_id)["status"], "skipped")

    async def test_revision_changed_while_waiting_is_not_approved_by_stale_batch(self):
        first_id, second_id = self.create("first", "review"), self.create("second", "review")
        entered, release = asyncio.Event(), asyncio.Event()
        sent = []

        async def send(row, payload):
            sent.append(row["id"])
            entered.set()
            await release.wait()
            return "123"

        task = asyncio.create_task(DeliveryCoordinator(lambda row: row["title"], send).deliver_batch(
            2, status="held", approve=True, actor="owner"))
        await entered.wait()
        self.create("second", "review", title="Changed after review selection")
        release.set()
        stats = await task
        self.assertEqual(sent, [first_id])
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(storage.get_item_by_id(second_id)["status"], "held")

    async def test_preparation_failure_is_bounded_and_does_not_wedge_batch(self):
        bad_id, good_id = self.create("bad"), self.create("good")
        sent = []

        def prepare(row):
            if row["id"] == bad_id:
                raise ValueError("secret=https://" + "user:password@example.com/" + "x" * 10000)
            return row["title"]

        async def send(row, payload):
            sent.append(row["id"])
            return "123"

        coordinator = DeliveryCoordinator(prepare, send)
        stats = await coordinator.deliver_batch(2)
        self.assertEqual((stats["posted"], stats["failed"]), (1, 1))
        self.assertEqual(sent, [good_id])
        bad = storage.get_item_by_id(bad_id)
        self.assertEqual(bad["post_attempts_count"], 1)
        self.assertNotIn("password", bad["last_post_error"])
        self.assertEqual(storage.retry_failed_items(max_attempts=2), 1)
        await coordinator.deliver_batch(2)
        self.assertEqual(storage.retry_failed_items(max_attempts=2), 0)

    async def test_send_timeout_is_uncertain_and_never_automatically_retried(self):
        item_id = self.create()
        calls = []

        async def send(row, payload):
            calls.append(row["id"])
            raise TimeoutError("lost response after Discord may have accepted")

        coordinator = DeliveryCoordinator(lambda row: "payload", send)
        result = await coordinator.deliver_one(item_id)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(storage.retry_failed_items(), 0)
        await coordinator.deliver_batch()
        await coordinator.deliver_one(item_id, approve=True, actor="owner")
        self.assertEqual(calls, [item_id])

    async def test_receipt_commit_failure_is_uncertain(self):
        item_id = self.create()

        async def send(row, payload):
            return "123"

        with patch.object(storage, "mark_item_posted", side_effect=sqlite_error()):
            result = await DeliveryCoordinator(lambda row: "payload", send).deliver_one(item_id)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "uncertain")

    async def test_failed_finalizer_mismatch_is_not_reported_as_safe_failure(self):
        item_id = self.create()

        def prepare(row):
            raise ValueError("Invalid content")

        async def send(row, payload):
            self.fail("Preparation failure must not reach send")

        with patch.object(storage, "mark_item_failed", return_value=False):
            result = await DeliveryCoordinator(prepare, send).deliver_one(item_id)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "sending")

    async def test_claim_recovered_during_async_preparation_is_not_sent(self):
        item_id = self.create()

        async def prepare(row):
            storage.recover_interrupted_claims()
            return "prepared"

        async def send(row, payload):
            self.fail("Recovered claim must not reach send")

        result = await DeliveryCoordinator(prepare, send).deliver_one(item_id)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "uncertain")

    async def test_preparation_cannot_rewrite_claimed_material(self):
        item_id = self.create()

        def prepare(row):
            row["title"] = "An unapproved replacement"
            return row["title"]

        async def send(row, payload):
            self.fail("Changed material must not reach send")

        result = await DeliveryCoordinator(prepare, send).deliver_one(item_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(storage.get_item_by_id(item_id)["title"], "one")

    async def test_cancelled_inflight_send_retains_uncertain_durable_claim(self):
        item_id = self.create()
        entered = asyncio.Event()

        async def send(row, payload):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(DeliveryCoordinator(lambda row: "payload", send).deliver_one(item_id))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "uncertain")

    async def test_owner_can_reconcile_receipt_or_skip_with_audit(self):
        item_id = self.create()
        storage.claim_item(item_id)
        storage.recover_interrupted_claims()
        self.assertTrue(storage.resolve_uncertain(item_id, "posted", message_id="987", expected_revision=1))
        self.assertEqual(storage.get_item_by_id(item_id)["discord_message_id"], "987")
        skipped = self.create("skip")
        storage.claim_item(skipped)
        storage.recover_interrupted_claims()
        self.assertTrue(storage.resolve_uncertain(skipped, "skip"))
        self.assertEqual(storage.get_item_by_id(skipped)["status"], "skipped")
        self.assertTrue(storage.verify_audit_chain()["ok"])


def sqlite_error():
    import sqlite3
    return sqlite3.OperationalError("disk full fixture")


if __name__ == "__main__":
    unittest.main()
