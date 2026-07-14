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


if __name__ == "__main__":
    unittest.main()
