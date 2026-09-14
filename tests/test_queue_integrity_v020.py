import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import storage


def item(**changes):
    value = {
        "source_id": "example", "provider_id": "rss", "source": "Example",
        "category": "announcements", "title": "A release", "url": "https://example.com/item",
        "external_id": "release-1", "summary": "Details", "tags": ["release"],
        "published_at": "2026-09-14T12:00:00Z", "image_url": "https://example.com/image.png",
        "delivery_mode": "review", "channel_id": "123", "role_id": "456",
        "attribution_label": "Publisher", "attribution_url": "https://example.com/",
    }
    value.update(changes)
    return value


class QueueIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = storage.HERALD_DB_PATH
        self.path = Path(self.temp.name) / "queue.db"
        storage.HERALD_DB_PATH = str(self.path)

    def tearDown(self):
        storage.HERALD_DB_PATH = self.old_path
        self.temp.cleanup()

    def create(self, **changes):
        return storage.upsert_item(item(**changes))["id"]

    def test_review_approval_bound_to_all_material_fields(self):
        replacements = {
            "title": "Changed title", "url": "https://example.com/changed", "summary": "Changed",
            "tags": ["different"], "published_at": "2026-09-15", "image_url": "https://example.com/new.png",
            "source": "Renamed publisher", "channel_id": "999", "role_id": "888",
            "attribution_label": "New attribution", "attribution_url": "https://example.com/new",
            "source_url": "https://example.com/home", "private": True,
            "source_policy_digest": "updated-source-configuration-digest",
        }
        for field, replacement in replacements.items():
            with self.subTest(field=field):
                original = item(external_id=field)
                first = storage.upsert_item(original)
                self.assertTrue(storage.approve_item(first["id"], expected_revision=1))
                changed = storage.upsert_item({**original, field: replacement})
                row = storage.get_item_by_id(first["id"])
                self.assertEqual(changed["id"], first["id"])
                self.assertEqual(row["revision"], 2)
                self.assertEqual(row["status"], "held")
                self.assertIsNone(row["approval_revision"])
                self.assertIsNone(storage.claim_item(first["id"]))
                self.assertFalse(storage.approve_item(first["id"], expected_revision=1))
                self.assertEqual(storage.list_audit_events_for_item(first["id"])[-1]["event_type"], "item_revised")
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_automatic_revision_authorises_new_digest_and_review_switch_holds(self):
        item_id = self.create(delivery_mode="automatic")
        storage.upsert_item(item(delivery_mode="automatic", title="Updated"))
        row = storage.get_item_by_id(item_id)
        self.assertEqual((row["status"], row["revision"], row["approval_revision"]), ("pending", 2, 2))
        storage.upsert_item(item(delivery_mode="review", title="Updated"))
        row = storage.get_item_by_id(item_id)
        self.assertEqual((row["status"], row["revision"], row["approval_revision"]), ("held", 3, None))

    def test_automatic_source_honours_explicit_startup_hold_or_skip(self):
        for admission in ("held", "skipped", "pending"):
            with self.subTest(admission=admission):
                value = item(external_id=admission, delivery_mode="automatic")
                item_id = storage.upsert_item(value, status=admission)["id"]
                row = storage.get_item_by_id(item_id)
                self.assertEqual(row["status"], admission)
                self.assertEqual(row["approval_revision"], 1 if admission == "pending" else None)
                storage.upsert_item(value, status="pending")
                self.assertEqual(storage.get_item_by_id(item_id), row)
        review = storage.upsert_item(item(external_id="review-admission"), status="pending")
        self.assertEqual(review["status"], "held")
        self.assertIsNone(storage.get_item_by_id(review["id"])["approval_revision"])

    def test_unchanged_rediscovery_deduplicates_without_audit_or_reapproval(self):
        item_id = self.create()
        storage.approve_item(item_id)
        count = storage.count_audit_events()
        again = storage.upsert_item(item())
        self.assertEqual((again["id"], again["revision"], again["status"]), (item_id, 1, "pending"))
        self.assertEqual(storage.count_audit_events(), count)

    def assert_fresh_delivery_metadata(self, row):
        self.assertEqual(row["post_attempts_count"], 0)
        for field in ("last_post_attempt", "last_post_error", "discord_message_id", "claim_token", "claimed_at"):
            self.assertEqual(row[field], "", field)
        self.assertIsNone(row["claimed_revision"])

    def fail_claim(self, item_id, revision):
        claim = storage.claim_item(item_id)
        self.assertIsNotNone(claim)
        self.assertTrue(storage.mark_item_failed(
            item_id, "Old preparation error", claim_token=claim["claim_token"], revision=revision,
        ))

    def test_material_revision_restores_full_automatic_retry_budget(self):
        item_id = self.create(delivery_mode="automatic")
        for attempt in range(1, 4):
            self.fail_claim(item_id, 1)
            self.assertEqual(storage.retry_failed_items(max_attempts=3), int(attempt < 3))
        previous = storage.get_item_by_id(item_id)
        self.assertEqual(previous["post_attempts_count"], 3)
        self.assertTrue(previous["last_post_attempt"])
        self.assertTrue(previous["claim_token"])

        storage.upsert_item(item(delivery_mode="automatic", title="New revision"))
        row = storage.get_item_by_id(item_id)
        self.assertEqual((row["status"], row["revision"], row["approval_revision"]), ("pending", 2, 2))
        self.assert_fresh_delivery_metadata(row)
        for attempt in range(1, 4):
            self.fail_claim(item_id, 2)
            self.assertEqual(storage.get_item_by_id(item_id)["post_attempts_count"], attempt)
            self.assertEqual(storage.retry_failed_items(max_attempts=3), int(attempt < 3))
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_material_revision_cleans_held_review_metadata_and_stale_receipt(self):
        item_id = self.create()
        self.assertTrue(storage.approve_item(item_id))
        self.fail_claim(item_id, 1)
        self.assertTrue(storage.set_item_status(item_id, "held", actor="owner"))
        # A revisable legacy row may retain a receipt from before claim validation.
        with storage.connect() as conn:
            conn.execute("UPDATE herald_items SET discord_message_id='987' WHERE id=?", (item_id,))
            conn.commit()
        storage.upsert_item(item(title="Reviewed replacement"))
        row = storage.get_item_by_id(item_id)
        self.assertEqual((row["status"], row["revision"], row["approval_revision"]), ("held", 2, None))
        self.assert_fresh_delivery_metadata(row)
        self.assertIsNone(storage.claim_item(item_id))

    def test_unchanged_rediscovery_preserves_legitimate_delivery_attempt_metadata(self):
        for status in ("failed", "pending", "held", "skipped"):
            with self.subTest(status=status):
                original = item(external_id=status, delivery_mode="automatic")
                item_id = storage.upsert_item(original)["id"]
                self.fail_claim(item_id, 1)
                if status != "failed":
                    self.assertTrue(storage.set_item_status(item_id, status, actor="owner"))
                before = storage.get_item_by_id(item_id)
                count = storage.count_audit_events()
                storage.upsert_item(original)
                self.assertEqual(storage.get_item_by_id(item_id), before)
                self.assertEqual(storage.count_audit_events(), count)

    def test_scoped_identity_and_distinct_external_ids_preserve_unrelated_rows(self):
        ids = {
            self.create(), self.create(source_id="other"), self.create(category="other"),
            self.create(external_id="release-2"), self.create(provider_id="local"),
        }
        self.assertEqual(len(ids), 5)
        self.assertEqual(self.create(external_id=""), min(ids))

    def test_different_providers_cannot_overwrite_by_external_id_or_url_fallback(self):
        for external_id in ("shared-id", ""):
            with self.subTest(external_id=external_id):
                original = item(external_id=external_id, source_id="scope-" + external_id)
                first = storage.upsert_item(original)["id"]
                self.assertTrue(storage.approve_item(first))
                frozen_approval = storage.get_item_by_id(first)
                second = storage.upsert_item({**original, "provider_id": "local", "title": "Unrelated provider"})
                self.assertTrue(second["created"])
                self.assertNotEqual(first, second["id"])
                self.assertEqual(storage.get_item_by_id(first), frozen_approval)
                self.assertEqual(storage.upsert_item(original)["id"], first)
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_skipped_is_not_resurrected_by_discovery(self):
        item_id = self.create(delivery_mode="automatic")
        self.assertTrue(storage.set_item_status(item_id, "skipped", actor="owner"))
        storage.upsert_item(item(delivery_mode="automatic"))
        storage.upsert_item(item(delivery_mode="automatic", title="Revision after skip"))
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "skipped")
        self.assertIsNone(storage.claim_item(item_id, approve=True, actor="owner"))

    def test_posted_inflight_and_uncertain_content_is_frozen(self):
        for final_status in ("sending", "posted", "uncertain"):
            with self.subTest(status=final_status):
                original = item(external_id=final_status, delivery_mode="automatic")
                item_id = storage.upsert_item(original)["id"]
                claimed = storage.claim_item(item_id)
                if final_status == "posted":
                    storage.mark_item_posted(item_id, "789", claim_token=claimed["claim_token"], revision=1)
                if final_status == "uncertain":
                    storage.mark_item_uncertain(item_id, "Timeout", claim_token=claimed["claim_token"], revision=1)
                frozen = storage.get_item_by_id(item_id)
                storage.upsert_item({**original, "title": "Bad replacement", "channel_id": "999"})
                self.assertFalse(storage.set_item_status(item_id, "skipped", actor="owner"))
                self.assertFalse(storage.mark_item_failed(item_id, "unsafe unclaimed failure"))
                self.assertFalse(storage.mark_item_posted(item_id, "unsafe unclaimed receipt"))
                self.assertEqual(storage.get_item_by_id(item_id), frozen)

    def test_concurrent_sqlite_claims_have_exactly_one_winner(self):
        item_id = self.create(delivery_mode="automatic")
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: storage.claim_item(item_id), range(12)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)
        self.assertEqual(storage.get_item_by_id(item_id)["post_attempts_count"], 1)
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_claim_finish_requires_matching_token_and_revision(self):
        item_id = self.create(delivery_mode="automatic")
        claim = storage.claim_item(item_id)
        self.assertFalse(storage.mark_item_posted(item_id, "100", claim_token="wrong", revision=1))
        self.assertFalse(storage.mark_item_posted(item_id, "100", claim_token=claim["claim_token"], revision=2))
        self.assertTrue(storage.mark_item_posted(item_id, "100", claim_token=claim["claim_token"], revision=1))
        self.assertFalse(storage.mark_item_failed(item_id, "late failure", claim_token=claim["claim_token"], revision=1))

    def test_tampered_material_digest_cannot_be_claimed(self):
        item_id = self.create(delivery_mode="automatic")
        with storage.connect() as conn:
            conn.execute("UPDATE herald_items SET title='unapproved' WHERE id=?", (item_id,))
            conn.commit()
        self.assertIsNone(storage.claim_item(item_id))

    def test_recovery_is_explicit_and_uncertainty_requires_owner_resolution(self):
        item_id = self.create(delivery_mode="automatic")
        storage.claim_item(item_id)
        storage.init_db()
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "sending")
        self.assertEqual(storage.recover_interrupted_claims(), 1)
        self.assertEqual(storage.recover_interrupted_claims(), 0)
        self.assertEqual(storage.retry_failed_items(), 0)
        self.assertFalse(storage.approve_item(item_id))
        self.assertFalse(storage.resolve_uncertain(item_id, "posted"))
        self.assertFalse(storage.resolve_uncertain(item_id, "retry", actor="herald"))
        self.assertFalse(storage.resolve_uncertain(item_id, "retry", expected_revision=2))
        self.assertTrue(storage.resolve_uncertain(item_id, "retry", expected_revision=1))
        self.assertEqual(storage.get_item_by_id(item_id)["status"], "pending")
        self.assertIsNotNone(storage.claim_item(item_id))

    def test_uncertainty_validation_accepts_only_exact_names_and_ascii_receipts(self):
        for message_id in ("0", "1", "12345678901234567890"):
            with self.subTest(valid_message_id=message_id):
                self.assertTrue(storage.valid_uncertainty_resolution("posted", message_id))
        for message_id in ("", " ", " 123", "123 ", "+123", "-123", "1.0", "1e3", "1\n", "１２３", "١٢٣", "²", "1" * 21, 123, None, []):
            with self.subTest(invalid_message_id=message_id):
                self.assertFalse(storage.valid_uncertainty_resolution("posted", message_id))
        for resolution in ("", "POSTED", " posted", "retry ", "delete", None, []):
            with self.subTest(invalid_resolution=resolution):
                self.assertFalse(storage.valid_uncertainty_resolution(resolution, "123"))
        self.assertTrue(storage.valid_uncertainty_resolution("retry"))
        self.assertTrue(storage.valid_uncertainty_resolution("skip"))

    def test_uncertainty_backend_rejects_invalid_or_stale_resolution_without_mutation(self):
        item_id = self.create(delivery_mode="automatic")
        claim = storage.claim_item(item_id)
        self.assertTrue(storage.mark_item_uncertain(item_id, "Timeout", claim_token=claim["claim_token"], revision=1))
        before = storage.get_item_by_id(item_id)
        count = storage.count_audit_events()
        for resolution, message_id in (("posted", "۱۲۳"), ("posted", "123 "), ("posted", "1" * 21), ("unknown", "123")):
            with self.subTest(resolution=resolution, message_id=message_id):
                self.assertFalse(storage.resolve_uncertain(item_id, resolution, message_id=message_id))
                self.assertEqual(storage.get_item_by_id(item_id), before)
                self.assertEqual(storage.count_audit_events(), count)
        self.assertFalse(storage.resolve_uncertain(item_id, "posted", message_id="123", expected_revision=2))
        self.assertFalse(storage.resolve_uncertain(item_id, "retry", actor="herald", expected_revision=1))
        self.assertEqual(storage.get_item_by_id(item_id), before)
        self.assertTrue(storage.resolve_uncertain(item_id, "posted", message_id="123", expected_revision=1))
        after = storage.get_item_by_id(item_id)
        self.assertEqual((after["status"], after["discord_message_id"]), ("posted", "123"))
        self.assertFalse(storage.resolve_uncertain(item_id, "retry", expected_revision=1))
        self.assertEqual(storage.get_item_by_id(item_id), after)

    def test_uncertainty_retry_audit_records_duplicate_risk_acknowledgement(self):
        item_id = self.create(delivery_mode="automatic")
        storage.claim_item(item_id)
        storage.recover_interrupted_claims()
        self.assertTrue(storage.resolve_uncertain(item_id, "retry", expected_revision=1))
        event = storage.list_audit_events_for_item(item_id)[-1]
        self.assertEqual(event["reason"], "owner_reconciled_retry")
        self.assertTrue(storage._safe_json_loads(event["payload_json"])["duplicate_risk_acknowledged"])

    def test_audit_output_escapes_provider_markdown_without_changing_raw_history(self):
        examples = (
            ("[brackets]", r"\[brackets\]"),
            ("(parentheses)", r"\(parentheses\)"),
            ("`code`", r"\`code\`"),
            ("[Trusted Update](https://evil.example)", r"\[Trusted Update\]\(https://evil\.example\)"),
            ("**bold** _italic_ ||hidden|| ~~strike~~ > #", r"\*\*bold\*\* \_italic\_ \|\|hidden\|\| \~\~strike\~\~ \> \#"),
        )
        for index, (raw, escaped) in enumerate(examples):
            with self.subTest(raw=raw):
                item_id = self.create(external_id=str(index), title=raw, source=raw, category=raw)
                storage.audit_event(raw, item_id=item_id, actor=raw, reason=raw, detail=raw,
                                    payload={"title": raw, "source": raw, "category": raw})
                item_before = storage.get_item_by_id(item_id)
                events_before = storage.list_audit_events_for_item(item_id)
                item_text = storage.audit_item_text(item_id)
                self.assertIn(f"Title: **{escaped}**", item_text)
                self.assertIn(f"Source: {escaped}", item_text)
                self.assertIn(f"Category: {escaped}", item_text)
                self.assertIn(f"detail: {escaped}", item_text)
                recent_text = storage.audit_recent_text(1)
                self.assertIn(f"{escaped}: {escaped}", recent_text)
                self.assertIn(f"category: {escaped}", recent_text)
                self.assertIn(f"actor: {escaped}", recent_text)
                self.assertIn(f"reason: {escaped}", recent_text)
                self.assertIn(f"- {escaped}: `", storage.audit_summary_text())
                self.assertEqual(storage.get_item_by_id(item_id), item_before)
                self.assertEqual(storage.list_audit_events_for_item(item_id), events_before)
                self.assertEqual(item_before["title"], raw)
                self.assertEqual(events_before[-1]["detail"], raw)
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_audit_detail_fallback_and_mentions_remain_literal(self):
        raw = "[Update](https://evil.example) @everyone <@123> <@&456>"
        item_id = self.create(title=raw)
        storage.audit_event("provider_error", item_id=item_id, detail=raw)
        for output in (storage.audit_item_text(item_id), storage.audit_recent_text(1)):
            self.assertIn(r"\[Update\]\(https://evil\.example\)", output)
            self.assertNotIn("@everyone", output)
            self.assertNotIn("<@123>", output)
            self.assertNotIn("<@&456>", output)
            self.assertIn("@\u200beveryone", output)
        self.assertEqual(storage.list_audit_events_for_item(item_id)[-1]["detail"], raw)

    def legacy_database(self, fail_migration=False):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript("""
                CREATE TABLE herald_items (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, source TEXT NOT NULL,
                  title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'held', external_id TEXT NOT NULL DEFAULT '',
                  tags TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '', feed_url TEXT NOT NULL DEFAULT '',
                  image_url TEXT NOT NULL DEFAULT '', discord_message_id TEXT NOT NULL DEFAULT '',
                  post_attempts_count INTEGER NOT NULL DEFAULT 0, last_post_attempt TEXT NOT NULL DEFAULT '',
                  last_post_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX idx_herald_items_source_external_id ON herald_items(source, external_id) WHERE external_id <> '';
                CREATE TABLE herald_audit_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, event_type TEXT NOT NULL,
                  actor TEXT NOT NULL DEFAULT 'herald', old_status TEXT NOT NULL DEFAULT '', new_status TEXT NOT NULL DEFAULT '',
                  reason TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '',
                  payload_hash TEXT NOT NULL DEFAULT '', previous_event_hash TEXT NOT NULL DEFAULT '',
                  event_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                INSERT INTO herald_items (category,source,title,url,status,external_id,discord_message_id)
                  VALUES ('stream_alerts','Legacy','Posted forever','https://example.com/old','posted','old','987');
                INSERT INTO herald_items (category,source,title,url,status,external_id)
                  VALUES ('security_alerts','Legacy','Pending review','https://example.com/new','pending','new');
            """)
            conn.row_factory = sqlite3.Row
            storage._insert_audit_event(conn, event_type="posted", item_id=1, payload={"historic": True})
            audit_before = dict(conn.execute("SELECT * FROM herald_audit_events").fetchone())
            if fail_migration:
                conn.execute("CREATE TRIGGER reject_migration BEFORE INSERT ON herald_audit_events BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
            conn.commit()
        return audit_before

    def test_v011_migration_is_idempotent_and_preserves_history_and_audit_bytes(self):
        audit_before = self.legacy_database()
        storage.init_db()
        posted = storage.get_item_by_id(1)
        self.assertEqual((posted["title"], posted["status"], posted["discord_message_id"]), ("Posted forever", "posted", "987"))
        self.assertEqual(storage.get_item_by_id(2)["status"], "held")
        self.assertEqual(storage.list_audit_events_for_item(1)[0], audit_before)
        count = storage.count_audit_events()
        storage.init_db()
        self.assertEqual(storage.count_audit_events(), count)
        self.assertEqual(storage.schema_version(), 2)
        self.assertTrue(storage.verify_audit_chain()["ok"])

    def test_failed_migration_rolls_back_schema_and_history(self):
        self.legacy_database(fail_migration=True)
        with self.assertRaises(sqlite3.IntegrityError):
            storage.init_db()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertNotIn("revision", {row[1] for row in conn.execute("PRAGMA table_info(herald_items)")})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM herald_items").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT status FROM herald_items WHERE id=2").fetchone()[0], "pending")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM herald_audit_events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
