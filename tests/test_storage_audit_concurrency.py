import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import storage


class AuditConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "herald.db"
        storage.HERALD_DB_PATH = str(self.db_path)
        storage.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_concurrent_audit_writes_remain_one_valid_chain(self):
        def write_event(index: int) -> str:
            return storage.audit_event(
                "concurrency_test",
                actor="test",
                detail=str(index),
                payload={"index": index},
            )

        with ThreadPoolExecutor(max_workers=12) as executor:
            hashes = list(executor.map(write_event, range(100)))

        self.assertEqual(len(set(hashes)), 100)
        self.assertEqual(storage.count_audit_events(), 100)
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_tampered_audit_event_fails_chain_verification(self):
        storage.audit_event(
            "tamper_test",
            actor="test",
            detail="original first event",
            payload={"index": 1},
        )
        storage.audit_event(
            "tamper_test",
            actor="test",
            detail="untouched second event",
            payload={"index": 2},
        )

        before = storage.verify_audit_chain()
        self.assertTrue(before["ok"])
        self.assertEqual(before["checked"], 2)

        with storage.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM herald_audit_events
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()

            first_id = int(row["id"])

            conn.execute(
                """
                UPDATE herald_audit_events
                SET detail = ?
                WHERE id = ?
                """,
                ("tampered after insertion", first_id),
            )
            conn.commit()

        after = storage.verify_audit_chain()

        self.assertFalse(after["ok"])
        self.assertEqual(after["first_bad_event_id"], first_id)
        self.assertEqual(after["reason"], "event_hash mismatch")
        self.assertEqual(after["checked"], 1)


if __name__ == "__main__":
    unittest.main()
