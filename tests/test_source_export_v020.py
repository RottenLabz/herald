"""Real Git/archives and malicious runtime fixtures; no secrets or live services."""
import io
import gzip
import shutil
import stat
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

import source_export as export


class SourceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="herald-export-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic test")
        self.git("config", "user.email", "test@example.invalid")
        self.write(".gitignore", ".env\n*.tar.gz\n*.sha256\n")
        self.write(".env.example", "DISCORD_TOKEN=\nOWNER_ID=0\n")
        self.write("source_export.py", "# reviewed placeholder for fixture only\n")
        self.write("README.md", "# Public source\n")
        self.approve([".gitignore", ".env.example", "source_export.py", "README.md"])
        self.commit()
        self.output = self.root / "export.tar.gz"
        self.clean_env = mock.patch.dict(os.environ, {}, clear=True)
        self.clean_env.start()
        self.addCleanup(self.clean_env.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True).stdout

    def write(self, name, data):
        path = self.root / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")

    def approve(self, names):
        self.write(export.MANIFEST, "\n".join(sorted(set(names) | {export.MANIFEST})) + "\n")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "Synthetic fixture")

    def test_public_template_retained_untracked_private_omitted(self):
        self.write(".env", "DISCORD_TOKEN=synthetic-not-a-real-token\n")
        self.write("private-operator-notes.txt", "never reviewed for publication")
        export.export_source(self.root, self.output)
        with tarfile.open(self.output) as archive:
            names = set(archive.getnames())
        self.assertIn(".env.example", names)
        self.assertNotIn(".env", names)
        self.assertNotIn("private-operator-notes.txt", names)
        self.assertTrue(Path(str(self.output) + ".sha256").is_file())

    def test_dirty_tracked_tree_and_index_rejected(self):
        self.write("README.md", "Changed but not committed")
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)
        self.git("add", "README.md")
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_unreviewed_tracked_source_rejected(self):
        self.write("unexpected.txt", "Private arbitrary name")
        self.commit()
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)

    def test_custom_runtime_and_sidecars_excluded_even_if_tracked_approved(self):
        names = ["unusual.runtime", "unusual.runtime-wal", "unusual.runtime-shm", "unusual.runtime-journal",
                 "custom-feed-store", "custom.db", "custom.db-wal", "unusual.runtime.instance.lock"]
        for name in names:
            self.write(name, "synthetic runtime data")
        self.approve([".gitignore", ".env.example", "source_export.py", "README.md", *names])
        self.commit()
        self.write(".env", "HERALD_DB_PATH=unusual.runtime\nHERALD_FEED_CONFIG_PATH=custom-feed-store\n")
        export.export_source(self.root, self.output)
        with tarfile.open(self.output) as archive:
            self.assertFalse(set(names) & set(archive.getnames()))

    def test_sqlite_header_excluded_regardless_filename(self):
        for index, signature in enumerate(export.DB_SIGNATURES):
            self.write(f"looks-public-{index}.md", signature + b"synthetic pages")
        self.commit()
        export.export_source(self.root, self.output)
        with tarfile.open(self.output) as archive:
            self.assertFalse(any("looks-public" in n for n in archive.getnames()))

    def test_env_never_shell_executed(self):
        target = self.root / "would-be-created"
        self.write(".env", f"HERALD_NAME=$(touch {target})\nHERALD_DB_PATH=unusual.runtime\n")
        export.export_source(self.root, self.output)
        self.assertFalse(target.exists())

    def test_environment_runtime_path_and_private_provider_path_excluded(self):
        self.write("operator-module.py", "# private provider\n")
        self.write("runtime-config.md", "runtime contents")
        self.commit()
        with mock.patch.dict(os.environ, {
            "HERALD_FEED_CONFIG_PATH": str(self.root / "runtime-config.md"),
            "HERALD_PRIVATE_PROVIDERS": '[{"path":' + __import__("json").dumps(str(self.root / "operator-module.py")) + '}]',
        }):
            export.export_source(self.root, self.output)
        with tarfile.open(self.output) as archive:
            self.assertNotIn("operator-module.py", archive.getnames())
            self.assertNotIn("runtime-config.md", archive.getnames())

    def test_dotenv_preserves_non_shell_paths_and_quoted_json(self):
        self.write(".env", "HERALD_DB_PATH=C:\\existing\\state#1.runtime # comment\n"
                   "HERALD_PRIVATE_PROVIDERS='[{\"path\":\"/existing/private\"}]'\n")
        values = export.read_env_file(self.root / ".env")
        self.assertEqual(values["HERALD_DB_PATH"], r"C:\existing\state#1.runtime")
        self.assertEqual(__import__("json").loads(values["HERALD_PRIVATE_PROVIDERS"])[0]["path"], "/existing/private")

    def test_malformed_env_fails_closed(self):
        self.write(".env", 'HERALD_DB_PATH="unterminated\n')
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_secret_in_approved_source_refuses_export(self):
        self.write("README.md", "DISCORD_" + "TOKEN=synthetic-private-credential\n")
        self.commit()
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_scan_execution_failure_cannot_publish(self):
        with mock.patch.object(export, "scan_content", side_effect=OSError("scanner unavailable")):
            with self.assertRaises(OSError):
                export.export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_archive_verifier_failure_deletes_temporary_and_never_publishes(self):
        with mock.patch.object(export, "verify_archive", side_effect=OSError("read failed")):
            with self.assertRaises(OSError):
                export.export_source(self.root, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".herald-export-*")), [])

    def test_git_execution_failure_refuses(self):
        with mock.patch.object(export.subprocess, "run", side_effect=OSError("missing Git")):
            with self.assertRaises(export.ExportError):
                export.export_source(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_tracked_source_refused(self):
        (self.root / "linked.py").symlink_to("README.md")
        self.commit()
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)

    def malicious_archive(self, name, kind=None, twice=False):
        with tarfile.open(self.output, "w:gz") as archive:
            member = tarfile.TarInfo(name)
            if kind:
                member.type = kind
                member.linkname = "README.md"
                archive.addfile(member)
            else:
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
                if twice:
                    archive.addfile(member, io.BytesIO(b"x"))

    def test_verifier_rejects_traversal_links_duplicates_extras_and_corruption(self):
        for name, kind, twice in [("../escape", None, False), ("/absolute", None, False),
                                  ("README.md", tarfile.SYMTYPE, False),
                                  ("README.md", None, True), ("unexpected.txt", None, False)]:
            with self.subTest(name=name, kind=kind, twice=twice):
                self.malicious_archive(name, kind, twice)
                with self.assertRaises(export.ExportError):
                    export.verify_archive(self.output, {"README.md": b"x"})
        self.output.write_bytes(b"not gzip")
        with self.assertRaises(export.ExportError):
            export.verify_archive(self.output, {"README.md": b"x"})

    def test_verifier_rejects_hidden_private_metadata(self):
        for attributes in ({"uname": "private-operator"}, {"pax_headers": {"comment": "private-credential"}}):
            with self.subTest(attributes=attributes):
                with tarfile.open(self.output, "w:gz") as archive:
                    member = tarfile.TarInfo("README.md")
                    member.size = 1
                    for name, value in attributes.items():
                        setattr(member, name, value)
                    archive.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(export.ExportError):
                    export.verify_archive(self.output, {"README.md": b"x"})

    def test_verifier_rejects_missing_or_changed_bytes(self):
        self.malicious_archive("README.md")
        with self.assertRaises(export.ExportError):
            export.verify_archive(self.output, {"README.md": b"z"})
        with self.assertRaises(export.ExportError):
            export.verify_archive(self.output, {"README.md": b"x", "missing.md": b"x"})

    def test_verifier_rejects_hidden_trailing_archive_payload(self):
        self.malicious_archive("README.md")
        legitimate = self.output.read_bytes()
        self.output.write_bytes(legitimate + b"hidden trailing data")
        with self.assertRaises(export.ExportError):
            export.verify_archive(self.output, {"README.md": b"x"})
        self.output.write_bytes(legitimate + gzip.compress(b"hidden second stream"))
        with self.assertRaises(export.ExportError):
            export.verify_archive(self.output, {"README.md": b"x"})
        raw_tar = gzip.decompress(legitimate)
        self.output.write_bytes(gzip.compress(raw_tar + b"hidden tar payload"))
        with self.assertRaises(export.ExportError):
            export.verify_archive(self.output, {"README.md": b"x"})

    @unittest.skipUnless(shutil.which("install"), "Linux install utility required")
    def test_documented_env_creation_protects_before_secrets(self):
        target = self.root / ".env"
        old_umask = os.umask(0o022)
        try:
            subprocess.run(["install", "-m", "0600", str(self.root / ".env.example"), str(target)], check=True)
        finally:
            os.umask(old_umask)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        # The restrictive file exists before any synthetic private value is added.
        with target.open("a") as handle:
            handle.write("# synthetic private value\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_existing_export_never_overwritten(self):
        self.output.write_bytes(b"keep original")
        with self.assertRaises(export.ExportError):
            export.export_source(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep original")


if __name__ == "__main__":
    unittest.main()
