"""Actual Discord adapters with mocked Discord I/O; no live service writes."""
import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

AVAILABLE = importlib.util.find_spec("discord") is not None
if AVAILABLE:
    import discord
    import bot
    import config
    import storage
    import provider_runtime
    import slash_commands
    from providers.common import normalize_item
    from delivery import DeliveryCoordinator
    from presentation import escape_provider_text


@unittest.skipUnless(AVAILABLE, "BLOCKED BY ENVIRONMENT: real discord.py is unavailable")
class BotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        values = [(storage, "HERALD_DB_PATH", str(Path(self.temp.name) / "queue.db")),
                  (config, "HERALD_FEED_CONFIG_PATH", str(Path(self.temp.name) / "feeds.json")),
                  (config, "HERALD_PRIVATE_PROVIDERS", []),
                  (config, "FREE_GAMES_CHANNEL_ID", 202), (config, "FREE_GAMES_ROLE_ID", 0),
                  (config, "FREE_GAMES_ENABLED", True), (config, "HERALD_GUILD_ID", 303),
                  (config, "OWNER_ID", 101), (config, "HERALD_COMMAND_SCOPE", "guild"),
                  (bot, "HERALD_GUILD_ID", 303), (bot, "OWNER_ID", 101)]
        for module, name, value in values:
            handle = patch.object(module, name, value)
            handle.start()
            self.addCleanup(handle.stop)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 202
        self.channel.send = AsyncMock(return_value=SimpleNamespace(id=404))
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
        self.guild = SimpleNamespace(id=303, me=object(), get_channel=lambda _: self.channel)
        self.channel.guild = self.guild
        resolver = patch.object(bot, "configured_guild", return_value=self.guild)
        resolver.start()
        self.addCleanup(resolver.stop)
        self.source = provider_runtime.get_configured_sources()[0]
        self.item = normalize_item({"title": "A free game", "url": "https://example.com/claim", "external_id": "one",
                                    "summary": "Free on PC", "tags": []}, self.source)
        result = storage.upsert_item(self.item, status="pending")
        self.row = storage.get_item_by_id(result["id"])
        coordinator = patch.object(bot, "delivery_coordinator", DeliveryCoordinator(bot.prepare_delivery, bot.send_delivery))
        coordinator.start()
        self.addCleanup(coordinator.stop)

    async def test_gamerpower_both_clickable_links_and_safe_mentions(self):
        ok, _ = await bot.post_item_to_discord(self.row)
        self.assertTrue(ok)
        kwargs = self.channel.send.await_args.kwargs
        rendered = str(kwargs["embed"].to_dict()) + kwargs["content"]
        self.assertIn("https://example.com/claim", rendered)
        self.assertIn("https://www.gamerpower.com/", rendered)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_welcome_message_uses_independent_welcome_slot(self):
        member = SimpleNamespace(guild=SimpleNamespace(name="Example guild"), mention="<@101>")
        with patch.dict(bot.HERALD_EMOJIS, {"herald": "📣", "welcome": "🌻"}):
            rendered = bot.build_welcome_message(member)
        self.assertTrue(rendered.startswith("🌻 Welcome"))
        self.assertNotIn("📣", rendered)

    def resolution_command(self):
        client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(client.close)
        tree = slash_commands.install_commands(client, bot)
        return tree.get_command("herald", guild=discord.Object(id=303)).get_command("queue").get_command("resolve")

    async def resolve_both(self, command, resolution, message_id=""):
        message = SimpleNamespace(author=SimpleNamespace(id=101), guild=None,
                                  channel=SimpleNamespace(send=AsyncMock()))
        await bot.handle_owner_command(message, f"resolve {self.row['id']} {resolution} {message_id}")
        current = self.owner_interaction()
        await command.callback(current, self.row["id"], resolution, True, message_id)
        kwargs = current.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_dm_slash_resolution_reject_invalid_input_equally(self):
        command = self.resolution_command()
        cases = [("posted", value) for value in ("", "not-an-id", "１２３", "١٢٣", "+123", "-123", "1.2", "1" * 21)]
        cases += [(value, "123") for value in ("delete", "POSTED", "unknown")]
        for resolution, message_id in cases:
            with self.subTest(resolution=resolution, message_id=message_id), \
                    patch.object(storage, "resolve_uncertain") as resolve, \
                    patch.object(storage, "get_item_by_id") as get_item:
                await self.resolve_both(command, resolution, message_id)
                resolve.assert_not_called()
                get_item.assert_not_called()

    async def test_dm_slash_resolution_bind_same_current_revision(self):
        command = self.resolution_command()
        for resolution, message_id in (("posted", "1"), ("posted", "12345678901234567890"), ("retry", ""), ("skip", "")):
            with self.subTest(resolution=resolution, message_id=message_id), \
                    patch.object(storage, "resolve_uncertain", return_value=True) as resolve, \
                    patch.object(storage, "get_item_by_id", return_value={**self.row, "revision": 9}):
                await self.resolve_both(command, resolution, message_id)
                self.assertEqual(resolve.call_count, 2)
                for call in resolve.call_args_list:
                    self.assertEqual(call.args, (self.row["id"], resolution))
                    self.assertEqual(call.kwargs, {"message_id": message_id, "expected_revision": 9, "actor": "owner"})

    async def test_dm_slash_resolution_rechecks_state_and_revision(self):
        command = self.resolution_command()
        # The real storage transition must reject a pending row from either adapter.
        await self.resolve_both(command, "posted", "123")
        self.assertEqual(storage.get_item_by_id(self.row["id"])["status"], "pending")
        claim = storage.claim_item(self.row["id"])
        storage.mark_item_uncertain(self.row["id"], "fixture", claim_token=claim["claim_token"],
                                    revision=claim["revision"])
        current = storage.get_item_by_id(self.row["id"])
        # Model a stale read before storage's atomic current-revision check.
        with patch.object(storage, "get_item_by_id", return_value={**current, "revision": current["revision"] + 1}):
            await self.resolve_both(command, "retry")
        self.assertEqual(storage.get_item_by_id(self.row["id"])["status"], "uncertain")

    async def test_actual_dm_and_automatic_post_share_claim(self):
        message = SimpleNamespace(author=SimpleNamespace(id=101), guild=None,
                                  channel=SimpleNamespace(send=AsyncMock()))
        await asyncio.gather(bot.handle_owner_command(message, f"post {self.row['id']}"),
                             bot.deliver_pending_items_once())
        self.channel.send.assert_awaited_once()
        self.assertEqual(storage.get_item_by_id(self.row["id"])["status"], "posted")

    def owner_interaction(self):
        return SimpleNamespace(user=SimpleNamespace(id=101), guild_id=303, guild=self.guild,
                               response=SimpleNamespace(is_done=Mock(return_value=True)),
                               followup=SimpleNamespace(send=AsyncMock()))

    async def test_actual_dm_and_slash_post_share_claim(self):
        client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(client.close)
        tree = slash_commands.install_commands(client, bot)
        command = tree.get_command("herald", guild=discord.Object(id=303)).get_command("queue").get_command("post")
        message = SimpleNamespace(author=SimpleNamespace(id=101), guild=None,
                                  channel=SimpleNamespace(send=AsyncMock()))
        await asyncio.gather(bot.handle_owner_command(message, f"post {self.row['id']}"),
                             command.callback(self.owner_interaction(), self.row["id"]))
        self.channel.send.assert_awaited_once()

    async def test_actual_review_and_automatic_post_share_claim(self):
        view = slash_commands.QueueReviewView(bot, self.row)
        await asyncio.gather(view.post.callback(self.owner_interaction()), bot.deliver_pending_items_once())
        self.channel.send.assert_awaited_once()

    async def test_payload_preparation_failure_does_not_block_next(self):
        second = storage.upsert_item({**self.item, "external_id": "two", "url": "https://example.com/two"}, status="pending")
        original = bot.prepare_delivery
        def prepare(item):
            if item["id"] == self.row["id"]:
                raise ValueError("malformed fixture")
            return original(item)
        bot.delivery_coordinator.prepare = prepare
        result = await bot.deliver_pending_items_once()
        self.assertEqual((result["failed"], result["posted"]), (1, 1))
        self.assertEqual(storage.get_item_by_id(second["id"])["status"], "posted")

    async def test_actual_delivery_continues_during_stalled_discovery(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def stalled(status):
            entered.set()
            await release.wait()
            return {"errors": [], "health": []}
        with patch.object(bot, "discover_items_async", side_effect=stalled):
            discovery = asyncio.create_task(bot.run_watcher_cycle())
            await entered.wait()
            try:
                result = await asyncio.wait_for(bot.deliver_pending_items_once(), 2)
                self.assertEqual(result["posted"], 1)
                self.assertFalse(discovery.done())
            finally:
                release.set()
                await discovery

    async def test_current_policy_changes_block_until_rediscovered(self):
        with patch.object(config, "FREE_GAMES_DELIVERY_MODE", "review"):
            result = await bot.deliver_pending_items_once()
        self.assertEqual(result["failed"], 1)
        self.channel.send.assert_not_awaited()

    async def test_legacy_text_and_shared_handler_fail_closed(self):
        for owner, guild in ((999, self.guild), (101, SimpleNamespace(id=999))):
            message = SimpleNamespace(author=SimpleNamespace(id=owner, bot=False), guild=guild,
                                      channel=Mock(spec=discord.TextChannel), mentions=[], content="herald post 1")
            with patch.object(bot, "HERALD_SERVER_COMMANDS_ENABLED", True), patch.object(bot, "HERALD_OWNER_ONLY", False):
                await bot.on_message(message)
                await bot.handle_owner_command(message, "post 1")
        self.channel.send.assert_not_awaited()

    async def test_masked_links_and_formatting_are_literal(self):
        content, embed = bot.build_post_payload_for_item({**self.item, "title": "[trusted](https://evil.example) **bold** @everyone"}, self.guild)
        self.assertIn(r"\[trusted\]\(https://evil\.example\)", embed.title)
        self.assertNotIn("@everyone", embed.title)
        self.assertEqual(escape_provider_text("[x](y)", 30), r"\[x\]\(y\)")


if __name__ == "__main__":
    unittest.main()
