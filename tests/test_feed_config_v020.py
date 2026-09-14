import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feed_config as feeds


def example_feed(**changes):
    return {"id": "example-feed", "name": "Example feed", "url": "https://example.com/feed.xml",
            "enabled": True, "channel_id": 123456789, "delivery_mode": "review",
            "role_id": None, "category": "security_alerts", "private": True, **changes}


class FeedConfigTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.path = Path(self.scratch.name) / "feeds.json"

    def tearDown(self):
        self.scratch.cleanup()

    def test_versioned_roundtrip_and_history_category(self):
        saved = feeds.save_config({"version": 1, "feeds": [example_feed()]}, self.path)
        self.assertEqual(feeds.load_config(self.path), saved)
        self.assertEqual(saved["feeds"][0]["category"], "security_alerts")
        if os.name == "posix":
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_missing_file_has_no_enabled_feeds(self):
        self.assertEqual(feeds.list_feeds(self.path), [])

    def test_validation_rejects_unknown_version_and_duplicate_ids(self):
        for invalid in ({"version": True, "feeds": []}, {"version": 2, "feeds": []},
                        {"version": 1, "feeds": [example_feed(), example_feed()]},
                        {"version": 1, "feeds": [], "ignored": True}):
            with self.subTest(invalid=invalid), self.assertRaises(feeds.FeedConfigError):
                feeds.save_config(invalid, self.path)
        self.assertFalse(self.path.exists())

    def test_bad_schemes_credentials_ids_modes_booleans_and_fields_rejected(self):
        for changes in ({"url": "file:///private"}, {"url": "https://" + "user:secret@example.com/feed"},
                        {"url": "https://example.com/\n"}, {"id": "../escape"}, {"id": "gamerpower"},
                        {"channel_id": 0}, {"channel_id": True}, {"role_id": "123"},
                        {"delivery_mode": "digest"}, {"delivery_mode": []}, {"enabled": "false"},
                        {"private": "true"}, {"name": "x" * 101}, {"tags": ["x"] * 17},
                        {"attribution_label": "Example"}, {"surprise": 1}):
            with self.subTest(changes=changes), self.assertRaises(feeds.FeedConfigError):
                feeds.validate_feed(example_feed(**changes))

    def test_write_is_atomic_on_replace_failure_and_does_not_leak_temporary(self):
        initial = feeds.save_config({"version": 1, "feeds": [example_feed()]}, self.path)
        with patch("feed_config.os.replace", side_effect=OSError("secret path")):
            with self.assertRaises(feeds.FeedConfigError) as caught:
                feeds.save_config({"version": 1, "feeds": []}, self.path)
        self.assertEqual(feeds.load_config(self.path), initial)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertNotIn("secret path", str(caught.exception))

    def test_fsync_failure_aborts_before_replace(self):
        feeds.save_config({"version": 1, "feeds": []}, self.path)
        original = self.path.read_bytes()
        with patch("feed_config.os.fsync", side_effect=OSError("failure")), patch("feed_config.os.replace") as replace:
            with self.assertRaises(feeds.FeedConfigError):
                feeds.save_config({"version": 1, "feeds": [example_feed()]}, self.path)
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_crud_without_deleting_history(self):
        feeds.add_feed(example_feed(), self.path)
        self.assertTrue(feeds.set_feed_enabled("example-feed", False, self.path))
        self.assertFalse(feeds.list_feeds(self.path)[0]["enabled"])
        self.assertTrue(feeds.remove_feed("example-feed", self.path))
        self.assertFalse(feeds.remove_feed("example-feed", self.path))
        self.assertEqual(feeds.list_feeds(self.path), [])

    def test_oversized_config_is_bounded_before_json_parse(self):
        self.path.write_bytes(b" " * (feeds.MAX_CONFIG_BYTES + 1))
        with patch("feed_config.json.loads") as parse:
            with self.assertRaises(feeds.FeedConfigError):
                feeds.load_config(self.path)
        parse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
