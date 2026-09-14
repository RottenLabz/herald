import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from instance_lock import acquire_instance, InstanceAlreadyRunningError


ACQUIRE_IN_CHILD = """
import sys
from instance_lock import acquire_instance, InstanceAlreadyRunningError
try:
    with acquire_instance(sys.argv[1]):
        print('acquired', flush=True)
except InstanceAlreadyRunningError:
    print('locked', flush=True)
    sys.exit(23)
"""


@unittest.skipUnless(os.name in {"posix", "nt"}, "No supported advisory lock on this platform")
class InstanceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "herald.db"
        self.lease = None

    def tearDown(self):
        if self.lease is not None:
            self.lease.close()
        self.temp.cleanup()

    def child(self, database):
        return subprocess.run(
            [sys.executable, "-c", ACQUIRE_IN_CHILD, str(database)],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=10, check=False,
        )

    def test_second_process_cannot_acquire_until_lifetime_handle_closes(self):
        self.lease = acquire_instance(self.database)
        blocked = self.child(self.database)
        self.assertEqual((blocked.returncode, blocked.stdout.strip()), (23, "locked"), blocked.stderr)
        self.lease.close()
        self.lease.close()  # Lifetime cleanup can safely run twice.
        admitted = self.child(self.database)
        self.assertEqual((admitted.returncode, admitted.stdout.strip()), (0, "acquired"), admitted.stderr)
        self.assertTrue(self.lease.path.exists())
        self.assertFalse(self.database.exists())

    def test_relative_and_absolute_paths_share_one_canonical_lock(self):
        self.lease = acquire_instance(self.database)
        relative = os.path.relpath(self.database, Path(__file__).resolve().parent.parent)
        blocked = self.child(relative)
        self.assertEqual(blocked.returncode, 23, blocked.stderr)
        self.assertEqual(self.lease.path, Path(str(self.database.resolve()) + ".instance.lock"))

    def test_process_exit_releases_lock_without_deleting_file(self):
        admitted = self.child(self.database)
        self.assertEqual(admitted.returncode, 0, admitted.stderr)
        self.lease = acquire_instance(self.database)
        self.assertTrue(self.lease.path.is_file())

    def test_crashed_process_releases_lifetime_lock(self):
        code = """
import sys
from instance_lock import acquire_instance
lease = acquire_instance(sys.argv[1])
print('ready', flush=True)
sys.stdin.read(1)
"""
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(self.database)],
            cwd=Path(__file__).resolve().parent.parent,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(InstanceAlreadyRunningError):
                acquire_instance(self.database)
            child.kill()
            child.communicate(timeout=10)
            self.lease = acquire_instance(self.database)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)

    @unittest.skipUnless(os.name == "posix", "POSIX permission and symlink assertions")
    def test_posix_lock_is_private_and_does_not_follow_a_symlink(self):
        target = Path(self.temp.name) / "unchanged.txt"
        target.write_text("do not change", encoding="utf-8")
        lock_path = Path(str(self.database) + ".instance.lock")
        lock_path.symlink_to(target)
        with self.assertRaises(RuntimeError):
            acquire_instance(self.database)
        self.assertEqual(target.read_text(encoding="utf-8"), "do not change")
        lock_path.unlink()
        self.lease = acquire_instance(self.database)
        self.assertEqual(stat.S_IMODE(self.lease.path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link assertion")
    def test_hard_link_lock_is_rejected_before_permission_changes(self):
        target = Path(self.temp.name) / "unchanged.txt"
        target.write_text("do not change", encoding="utf-8")
        target.chmod(0o644)
        os.link(target, Path(str(self.database) + ".instance.lock"))
        with self.assertRaises(RuntimeError):
            acquire_instance(self.database)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
