import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import provider_runtime
from providers import common, gamerpower, rss


def source(**changes):
    return {"id": "example-feed", "source_id": "example-feed", "provider_id": "rss",
            "name": "Example feed", "url": "https://example.com/feed.xml", "enabled": True,
            "channel_id": 123, "role_id": None, "delivery_mode": "review", "category": "security_alerts",
            "homepage_url": "https://example.com/", "attribution_label": "", "attribution_url": "",
            "tags": [], "private": False, **changes}


class FakeResponse:
    def __init__(self, chunks, status=200):
        self.chunks = chunks
        self.status_code = status
        self.inspected = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            self.inspected += 1
            yield chunk


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def get(self, url, **kwargs):
        self.kwargs = kwargs
        return self.response


class ProviderBoundaryTests(unittest.TestCase):
    def test_decoded_chunk_budget_stops_download_immediately(self):
        response = FakeResponse([b"x" * 16384] * 100)
        session = FakeSession(response)
        with self.assertRaisesRegex(common.ProviderBoundaryError, "decoded_body_limit"):
            common.fetch_bytes(source()["url"], session=session)
        self.assertEqual(response.inspected, common.MAX_BODY_BYTES // 16384 + 1)
        self.assertTrue(response.closed)
        self.assertEqual(session.kwargs["timeout"], (5.0, 5.0))
        self.assertFalse(session.kwargs["allow_redirects"])
        self.assertTrue(session.kwargs["stream"])

    def test_one_oversized_decoded_chunk_is_rejected(self):
        response = FakeResponse([b"x" * (common.MAX_BODY_BYTES + 1), b"unused"])
        with self.assertRaises(common.ProviderBoundaryError):
            common.fetch_bytes(source()["url"], session=FakeSession(response))
        self.assertEqual(response.inspected, 1)

    def test_http_failure_and_deadline_are_errors(self):
        with self.assertRaisesRegex(common.ProviderBoundaryError, "http_status_503"):
            common.fetch_bytes(source()["url"], session=FakeSession(FakeResponse([], 503)))
        with self.assertRaisesRegex(common.ProviderBoundaryError, "operation_deadline"):
            common.fetch_bytes(source()["url"], deadline=time.monotonic() - 1, session=FakeSession(FakeResponse([])))

    def test_field_bounds_applied_before_storage(self):
        item = common.normalize_item({"title": "x" * 50000, "url": "https://example.com/item", "summary": "x" * 50000,
                                     "external_id": "a" * 50000, "published_at": "a" * 50000,
                                     "tags": ["a" * 1000] * 1000, "image_url": "https://example.com/" + "x" * 3000}, source())
        self.assertEqual(len(item["title"]), 256)
        self.assertEqual(len(item["summary"]), 1500)
        self.assertLessEqual(len(item["external_id"]), 256)
        self.assertTrue(item["external_id"].startswith("sha256:"))
        self.assertEqual(len(item["published_at"]), 100)
        self.assertLessEqual(len(item["tags"]), 16)
        self.assertTrue(all(len(tag) <= 32 for tag in item["tags"]))
        self.assertEqual(item["image_url"], "")
        with self.assertRaises(common.ProviderBoundaryError):
            common.normalize_item({"title": "title", "url": "https://example.com/" + "x" * 3000}, source())

    def test_malformed_html_has_fixed_raw_processing_budget(self):
        begun = time.monotonic()
        output = common.clean_text("<" * 500000)
        self.assertLess(time.monotonic() - begun, 0.5)
        self.assertEqual(output, "")
        self.assertEqual(common.clean_text("<b>Hello</b> &amp; bye"), "Hello & bye")

    def test_markdown_is_literal_and_mentions_neutralized(self):
        escaped = common.escape_display_text("[Steam login](https://example.com/claim) **bold** @everyone")
        self.assertIn(r"\[Steam login\]\(https://example\.com/claim\)", escaped)
        self.assertIn(r"\*\*bold\*\*", escaped)
        self.assertNotIn("@everyone", escaped)

    def test_empty_failed_and_partial_rss_are_distinguished(self):
        with patch.object(rss, "fetch_bytes", return_value=b"<feed/>"), patch.object(rss, "parse_feed", return_value={"version": "atom10", "entries": [], "bozo": 0}):
            self.assertEqual(rss.fetch_source(source())["health"]["status"], "empty")
        with patch.object(rss, "fetch_bytes", side_effect=common.ProviderBoundaryError("http_status_503")):
            self.assertEqual(rss.fetch_source(source())["health"]["status"], "failed")
        parsed = {"version": "rss20", "bozo": 1, "bozo_exception": ValueError("private url"),
                  "entries": [{"title": "Good", "link": "https://example.com/a"}, {"title": "Broken"}]}
        with patch.object(rss, "fetch_bytes", return_value=b"bounded"), patch.object(rss, "parse_feed", return_value=parsed):
            result = rss.fetch_source(source())
        self.assertEqual(result["health"]["status"], "degraded")
        self.assertEqual(len(result["items"]), 1)
        self.assertNotIn("private url", json.dumps(result))

    def test_parser_only_receives_bounded_bytes_and_inspects_entry_limit(self):
        parsed = {"version": "rss20", "entries": [{"title": "Item", "link": "https://example.com/a"}] * 70}
        with patch.object(rss, "fetch_bytes", return_value=b"bounded"), patch.object(rss, "parse_feed", return_value=parsed) as parse:
            result = rss.fetch_source(source())
        parse.assert_called_once_with(b"bounded")
        self.assertEqual(len(result["items"]), common.MAX_ENTRIES)
        self.assertEqual(result["health"]["status"], "degraded")

    def test_gamerpower_preserves_claim_and_independent_attribution(self):
        payload = [{"id": 123, "title": "BioShock from Independent Studios", "platforms": "PC,Steam", "description": "Full game",
                    "open_giveaway_url": "https://example.com/claim", "gamerpower_url": "https://www.gamerpower.com/giveaway"}]
        with patch.object(gamerpower, "fetch_bytes", return_value=json.dumps(payload).encode()):
            result = gamerpower.fetch_source()
        item = result["items"][0]
        self.assertEqual(item["url"], "https://example.com/claim")
        self.assertEqual(item["attribution_url"], "https://www.gamerpower.com/giveaway")
        self.assertEqual(item["attribution_label"], "GamerPower")
        payload[0]["gamerpower_url"] = "https://example.com/spoof"
        with patch.object(gamerpower, "fetch_bytes", return_value=json.dumps(payload).encode()):
            item = gamerpower.fetch_source()["items"][0]
        self.assertEqual(item["attribution_url"], "https://www.gamerpower.com/")

    def test_long_external_ids_and_urls_do_not_alias_after_bounding(self):
        first = common.normalize_item({"title": "One", "url": "https://example.com/" + "x" * 270 + "?release=1"}, source())
        second = common.normalize_item({"title": "Two", "url": "https://example.com/" + "x" * 270 + "?release=2"}, source())
        self.assertNotEqual(first["external_id"], second["external_id"])
        literal = common.bounded_identity(first["external_id"])
        self.assertNotEqual(first["external_id"], literal)
        self.assertEqual(common.bounded_identity("<literal-id>"), "<literal-id>")

    def test_gamerpower_fallback_preserves_configured_policy_digest(self):
        configured = dict(gamerpower.configured_source(), policy_digest="a" * 64)
        parsed = {"version": "rss20", "entries": [{"title": "Game", "link": "https://example.com/claim"}]}
        with patch.object(gamerpower, "fetch_bytes", side_effect=ValueError("api failure")), patch.object(rss, "fetch_bytes", return_value=b"bounded"), patch.object(rss, "parse_feed", return_value=parsed):
            result = gamerpower.fetch_source(configured)
        self.assertEqual(result["health"]["status"], "degraded")
        self.assertEqual(result["items"][0]["source_policy_digest"], configured["policy_digest"])
        self.assertEqual(result["items"][0]["attribution_url"], "https://www.gamerpower.com/")

    def test_configured_policy_digest_changes_on_every_source_change(self):
        descriptor = source(provider_id="local", _plugin={"path": "/private/existing", "module": "synthetic"})
        with patch.object(provider_runtime, "list_feeds", return_value=[]), patch.object(provider_runtime, "_private_descriptors", return_value=[descriptor]):
            before = provider_runtime.get_configured_sources()[-1]
            self.assertEqual(before["policy_digest"], provider_runtime.get_configured_sources()[-1]["policy_digest"])
            for key, value in (("url", "https://example.com/new"), ("enabled", False), ("name", "New name"), ("channel_id", 456), ("role_id", 789), ("delivery_mode", "automatic"), ("category", "gpu_updates"), ("private", True), ("tags", ["new"])):
                original = descriptor[key]
                descriptor[key] = value
                changed = provider_runtime.get_configured_sources()[-1]
                self.assertNotEqual(before["policy_digest"], changed["policy_digest"], key)
                descriptor[key] = original
            descriptor["_plugin"]["module"] = "changed"
            after = provider_runtime.get_configured_sources()[-1]
            self.assertNotEqual(before["policy_digest"], after["policy_digest"])
            self.assertNotIn("_plugin", after)
            self.assertNotIn("/private/existing", json.dumps(after))

    def test_private_loading_disabled_by_empty_config_and_path_traversal_rejected(self):
        with patch.object(config, "HERALD_PRIVATE_PROVIDERS", []), patch.object(provider_runtime, "_load_local_module") as load:
            self.assertEqual(provider_runtime._private_descriptors(), [])
        load.assert_not_called()
        invalid = {"path": "/operator/path", "module": "../arbitrary", "source": {k:v for k,v in source().items() if k not in {"source_id", "provider_id"}}}
        with patch.object(config, "HERALD_PRIVATE_PROVIDERS", [invalid]):
            with self.assertRaises(ValueError):
                provider_runtime._private_descriptors()

    def test_private_error_text_is_not_disclosed(self):
        self.assertNotIn("secret", common.error_code(ValueError("https://secret@example.com/private")))
        self.assertNotIn("secret", common.error_code(common.ProviderBoundaryError("secret")))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_feeds_keep_healthy_results_and_failure(self):
        worker = provider_runtime.DiscoveryWorker()
        async def mock_fetch(current, timeout, preview=False):
            if current["id"] == "broken":
                return {"items": [], "health": common.health(current, "failed", error="http_status_503")}
            item = common.normalize_item({"title": "Good", "url": "https://example.com/item"}, current)
            return {"items": [item], "health": common.health(current, "healthy", 1)}
        with patch.object(provider_runtime, "_configured_descriptors", return_value=[source(), source(id="broken")]), patch.object(worker, "_run_one", side_effect=mock_fetch):
            result = await worker.run()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual([h["status"] for h in result["health"]], ["healthy", "failed"])

    async def test_worker_timeout_is_killed_reaped_and_delivery_can_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "synthetic.py").write_text("import time\ndef fetch_items():\n    time.sleep(30)\n    return []\n")
            descriptor = source(provider_id="local", _plugin={"path": temporary, "module": "synthetic"})
            worker = provider_runtime.DiscoveryWorker(source_deadline=0.2)
            deliveries = []
            async def existing_pending_delivery():
                await asyncio.sleep(0.02)
                deliveries.append("pending item progressed")
            with patch.object(provider_runtime, "_configured_descriptors", return_value=[descriptor]):
                result, _ = await asyncio.gather(worker.run(), existing_pending_delivery())
            self.assertEqual(deliveries, ["pending item progressed"])
            self.assertEqual(result["health"][0]["error"], "worker_deadline_exceeded")
            self.assertFalse(worker.running)

    async def test_synthetic_plugin_uses_shared_normalization_without_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "synthetic.py").write_text("def fetch_items():\n    return [{'title':'Synthetic', 'url':'https://example.com/item', 'external_id':'one', 'delivery_mode':'automatic', 'destination_channel_id':999}]\n")
            descriptor = source(provider_id="local", private=True, _plugin={"path": temporary, "module": "synthetic"})
            worker = provider_runtime.DiscoveryWorker()
            with patch.object(provider_runtime, "_configured_descriptors", return_value=[descriptor]):
                result = await worker.run("example-feed", preview=True)
            self.assertEqual(result["health"][0]["status"], "healthy")
            self.assertEqual(result["health"][0]["name"], "Private source")
            self.assertEqual(result["items"][0]["delivery_mode"], "review")
            self.assertEqual(result["items"][0]["destination_channel_id"], 123)
            self.assertEqual(worker.last_health, [])
            self.assertFalse(Path(temporary, "herald.db").exists())

    async def test_busy_discovery_does_not_accumulate_workers(self):
        worker = provider_runtime.DiscoveryWorker()
        async with worker._lock:
            with patch.object(worker, "_run_one") as run_one:
                result = await worker.run()
        self.assertTrue(result["busy"])
        run_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
