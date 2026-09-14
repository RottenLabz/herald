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

    async def test_actual_dm_and_automatic_post_share_claim(self):
        message = SimpleNamespace(author=SimpleNamespace(id=101), guild=None,
                                  channel=SimpleNamespace(send=AsyncMock()))
        await asyncio.gather(bot.handle_owner_command(message, f"post {self.row['id']}"),
                             bot.deliver_pending_items_once())
        self.channel.send.assert_awaited_once()
        self.assertEqual(storage.get_item_by_id(self.row["id"])["status"], "posted")

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
