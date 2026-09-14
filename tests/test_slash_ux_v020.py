"""Real discord.py objects, mocked I/O. No live Discord/API writes.

When discord.py is absent these tests are explicitly skipped, never replaced by
a pretend library. Run the entire class in the supported release environment.
"""
import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

DISCORD_AVAILABLE = importlib.util.find_spec("discord") is not None
if DISCORD_AVAILABLE:
    import discord
    import config
    import diagnostics
    import feed_config
    import provider_runtime
    import slash_commands as slash
    import storage
    from delivery import DeliveryCoordinator


def interaction(owner=101, guild_id=202):
    response = SimpleNamespace(is_done=Mock(return_value=False), send_message=AsyncMock(), defer=AsyncMock())
    async def deferred(**kwargs):
        response.is_done.return_value = True
    response.defer.side_effect = deferred
    return SimpleNamespace(user=SimpleNamespace(id=owner), guild_id=guild_id,
                           guild=SimpleNamespace(id=guild_id), response=response,
                           followup=SimpleNamespace(send=AsyncMock()))


@unittest.skipUnless(DISCORD_AVAILABLE, "BLOCKED BY ENVIRONMENT: real discord.py is unavailable")
class SlashUxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch.object(config, "OWNER_ID", 101),
                        patch.object(config, "HERALD_GUILD_ID", 202),
                        patch.object(config, "HERALD_COMMAND_SCOPE", "guild", create=True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db = patch.object(storage, "HERALD_DB_PATH", str(Path(self.temp.name) / "test.db"))
        db.start()
        self.addCleanup(db.stop)
        storage.init_db()
        self.client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(self.client.close)
        self.services = SimpleNamespace(client=self.client, configured_guild=lambda: SimpleNamespace(id=202),
                                        WATCHER_RUNTIME={}, delivery_coordinator=SimpleNamespace(
                                            deliver_one=AsyncMock(return_value={"status": "posted"}),
                                            deliver_batch=AsyncMock(return_value={})),
                                        run_watcher_cycle=AsyncMock(), post_subscription_panel=AsyncMock(),
                                        post_welcome_for_member=AsyncMock(return_value=(True, "ok")),
                                        build_post_payload_for_item=Mock(return_value=("Preview", discord.Embed(title="Example"))))

    def tree(self):
        return slash.install_commands(self.client, self.services)

    def command(self, *names):
        command = self.tree().get_command("herald", guild=discord.Object(id=202))
        for name in names:
            command = command.get_command(name)
        return command

    def add_item(self, *, mode="review", status="held"):
        result = storage.upsert_item({"provider_id": "synthetic", "source_id": "test", "source": "Example",
                                      "category": "announcements", "title": "Example", "url": "https://example.com/item",
                                      "summary": "Safe fixture", "delivery_mode": mode, "channel_id": 303}, status=status)
        return storage.get_item_by_id(result["id"])

    async def test_target_owner_allowed(self):
        self.assertTrue(await self.tree().interaction_check(interaction()))

    async def test_wrong_guild_denied_including_global(self):
        with patch.object(config, "HERALD_COMMAND_SCOPE", "global"):
            self.assertFalse(await self.tree().interaction_check(interaction(guild_id=999)))

    async def test_nonowner_and_dm_denied(self):
        self.assertFalse(await self.tree().interaction_check(interaction(owner=999)))
        self.assertFalse(await self.tree().interaction_check(interaction(guild_id=None)))

    async def test_mutation_callback_itself_rechecks_authorization(self):
        command = self.command("queue", "post")
        await command.callback(interaction(owner=999), 1)
        self.services.delivery_coordinator.deliver_one.assert_not_awaited()

    async def test_one_namespace_and_native_parameters_serialize(self):
        tree = self.tree()
        commands = tree.get_commands(guild=discord.Object(id=202))
        self.assertEqual([command.name for command in commands], ["herald"])
        payload = commands[0].to_dict(tree)
        self.assertEqual(payload["name"], "herald")
        queue = next(option for option in payload["options"] if option["name"] == "queue")
        self.assertIn("resolve", [option["name"] for option in queue["options"]])
        feed = next(option for option in payload["options"] if option["name"] == "feed")
        add = next(option for option in feed["options"] if option["name"] == "add")
        types = {option["name"]: option["type"] for option in add["options"]}
        self.assertEqual(types["channel"], discord.AppCommandOptionType.channel.value)
        self.assertEqual(types["role"], discord.AppCommandOptionType.role.value)

    async def test_local_registration_idempotent(self):
        self.assertIs(self.tree(), self.tree())
        self.assertEqual(len(self.tree().get_commands(guild=discord.Object(id=202))), 1)

    async def test_guild_sync_once_with_obsolete_global_cleanup(self):
        tree = self.tree()
        with patch.object(tree, "sync", new=AsyncMock(return_value=[])) as sync:
            await asyncio.gather(slash.synchronize_commands(self.client, self.services),
                                 slash.synchronize_commands(self.client, self.services))
            await slash.synchronize_commands(self.client, self.services)
            self.assertEqual(sync.await_count, 2)
            self.assertIsNone(sync.await_args_list[0].kwargs["guild"])
            self.assertEqual(sync.await_args_list[1].kwargs["guild"].id, 202)

    async def test_global_sync_without_local_duplicate(self):
        with patch.object(config, "HERALD_COMMAND_SCOPE", "global"):
            tree = self.tree()
            self.assertEqual(len(tree.get_commands()), 1)
            self.assertEqual(tree.get_commands(guild=discord.Object(id=202)), [])
            with patch.object(tree, "sync", new=AsyncMock(return_value=[])) as sync:
                await slash.synchronize_commands(self.client, self.services)
                self.assertEqual(sync.await_args_list[0].kwargs["guild"].id, 202)
                self.assertIsNone(sync.await_args_list[1].kwargs["guild"])

    async def test_registration_failure_sanitized(self):
        tree = self.tree()
        with patch.object(tree, "sync", new=AsyncMock(side_effect=RuntimeError("secret:https://private.example/token"))):
            with self.assertRaisesRegex(RuntimeError, "^Herald command registration failed$"):
                await slash.synchronize_commands(self.client, self.services)
        self.assertEqual(self.client._herald_registration_state, "sync_failed")

    async def test_owner_success_ephemeral(self):
        current = interaction()
        await self.command("help").callback(current)
        current.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertTrue(current.followup.send.await_args.kwargs["ephemeral"])
        self.assertEqual(current.followup.send.await_args.kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_review_post_rejects_changed_revision(self):
        item = self.add_item()
        view = slash.QueueReviewView(self.services, item)
        storage.upsert_item({**item, "title": "Revised"}, status="held")
        current = interaction()
        await view.post.callback(current)
        self.services.delivery_coordinator.deliver_one.assert_not_awaited()
        self.assertIn("stale", current.followup.send.await_args.args[0])

    async def test_review_skip_rejects_changed_revision(self):
        item = self.add_item()
        view = slash.QueueReviewView(self.services, item)
        storage.upsert_item({**item, "title": "Revised"}, status="held")
        await view.skip.callback(interaction())
        self.assertEqual(storage.get_item_by_id(item["id"])["status"], "held")

    async def test_review_inflight_is_honest(self):
        item = self.add_item()
        view = slash.QueueReviewView(self.services, item)
        storage.claim_item(item["id"], approve=True, actor="owner")
        current = interaction()
        await view.skip.callback(current)
        self.assertEqual(storage.get_item_by_id(item["id"])["status"], "sending")
        self.assertIn("sending", current.followup.send.await_args.args[0])

    async def test_review_and_automatic_share_one_claim(self):
        item = self.add_item(mode="automatic", status="pending")
        sends = []
        async def send(row, payload):
            sends.append(row["id"])
            await asyncio.sleep(0)
            return "40001"
        coordinator = DeliveryCoordinator(lambda row: row, send)
        self.services.delivery_coordinator = coordinator
        view = slash.QueueReviewView(self.services, item)
        await asyncio.gather(view.post.callback(interaction()), coordinator.deliver_one(item["id"]))
        self.assertEqual(sends, [item["id"]])

    async def test_slash_and_dm_wrapper_share_one_claim(self):
        item = self.add_item()
        sends = []
        async def send(row, payload):
            sends.append(row["id"])
            await asyncio.sleep(0)
            return "40002"
        coordinator = DeliveryCoordinator(lambda row: row, send)
        self.services.delivery_coordinator = coordinator
        # The owner DM adapter uses the same revision-bound coordinator call.
        await asyncio.gather(self.command("queue", "post").callback(interaction(), item["id"]),
                             coordinator.deliver_one(item["id"], approve=True, actor="owner", expected_revision=item["revision"]))
        self.assertEqual(sends, [item["id"]])

    async def test_preview_does_not_persist_or_post(self):
        result = {"items": [{"title": "Example", "url": "https://example.com/item"}],
                  "health": [{"status": "healthy"}]}
        with patch.object(provider_runtime, "fetch_source_preview", new=AsyncMock(return_value=result)), \
             patch.object(storage, "upsert_item") as upsert:
            current = interaction()
            await self.command("feed", "preview").callback(current, "example")
            upsert.assert_not_called()
            self.services.delivery_coordinator.deliver_one.assert_not_awaited()
            self.assertTrue(current.followup.send.await_args.kwargs["ephemeral"])
            self.assertIsInstance(current.followup.send.await_args.kwargs["embed"], discord.Embed)

    async def test_feed_add_saves_real_validated_config(self):
        target = str(Path(self.temp.name) / "feeds.json")
        channel = Mock(spec=discord.TextChannel)
        channel.id = 303
        channel.guild = SimpleNamespace(id=202)
        with patch.object(config, "HERALD_FEED_CONFIG_PATH", target, create=True):
            await self.command("feed", "add").callback(interaction(), "example", "Example feed",
                                                        "https://example.com/feed.xml", channel, "automatic")
            saved = feed_config.load_config()
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["feeds"][0]["delivery_mode"], "automatic")
        self.assertEqual(saved["feeds"][0]["channel_id"], 303)
        self.assertEqual(storage.count_items_by_status("pending"), 0)

    async def test_status_delivery_policy_counts(self):
        with patch.object(diagnostics, "source_policies", return_value=[
                {"delivery_mode": "automatic", "enabled": True},
                {"delivery_mode": "review", "enabled": True}]), \
             patch.object(diagnostics, "provider_health", return_value=[]):
            text = diagnostics.status_text(self.services)
        self.assertIn("automatic 1, review 1", text)

    async def test_uncertain_resolution_requires_acknowledgment(self):
        with patch.object(storage, "resolve_uncertain") as resolve:
            await self.command("queue", "resolve").callback(interaction(), 1, "retry", False)
            resolve.assert_not_called()

    async def test_doctor_never_reflects_private_fields_or_exceptions(self):
        secret = "sensitive-fixture-TOKEN"
        self.services.WATCHER_RUNTIME = {"last_error": secret, "last_provider_errors": [secret],
                                        "last_delivery_error": secret, "last_success": secret,
                                        "last_discovery": {"health": [{"status": "failed", "name": secret,
                                                                         "url": "https://private.example/" + secret,
                                                                         "error": secret}]}}
        with patch.object(diagnostics, "source_policies", return_value=[{"name": secret, "url": secret, "enabled": False}]), \
             patch.object(feed_config, "load_config", return_value={"version": 1, "feeds": []}), \
             patch.object(config, "HERALD_BUILD_ID", "/private/" + secret, create=True):
            result = diagnostics.doctor_text(self.services, self.client)
        self.assertNotIn(secret, result)
        self.assertNotIn("private.example", result)
        self.assertIn("Last discovery error", result)
        self.assertIn("raw details withheld", result)

    async def test_open_source_rejects_credentials_and_unsafe_schemes(self):
        self.assertIsNone(slash.source_url("https://" + "secret:token@example.com/feed"))
        self.assertIsNone(slash.source_url("javascript:alert(1)"))
        self.assertEqual(slash.source_url("https://example.com/item"), "https://example.com/item")

    async def test_error_handler_does_not_reflect_exception(self):
        current = interaction()
        await self.tree().on_error(current, RuntimeError("sensitive-fixture-TOKEN"))
        self.assertNotIn("sensitive-fixture-TOKEN", current.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
