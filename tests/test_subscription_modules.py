"""Original module regressions adapted to the configured-source select UI."""
import unittest

import discord
import subscriptions


class SubscriptionModuleTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        subscriptions.configure_subscriptions(0, ())

    async def test_view_only_builds_options_for_enabled_configured_sources(self):
        subscriptions.configure_subscriptions(42, [
            {"id": "free-games", "name": "Free Games", "enabled": True, "role_id": 10, "channel_id": 20},
            {"id": "announcements", "name": "Announcements", "enabled": False, "role_id": 11, "channel_id": 21},
            {"id": "project-news", "name": "Project News", "enabled": True, "role_id": 12, "channel_id": 22},
        ])
        view = subscriptions.SubscriptionView()
        self.assertIsInstance(view.children[0], discord.ui.Select)
        self.assertEqual([option.label for option in view.children[0].options], ["Free Games", "Project News"])
        self.assertEqual(view.children[1].custom_id, "herald_subscribe:v020:show_mine")
        self.assertTrue(view.is_persistent())

    async def test_empty_source_set_still_has_status_button(self):
        subscriptions.configure_subscriptions(42, [])
        view = subscriptions.SubscriptionView()
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].custom_id, "herald_subscribe:v020:show_mine")

    async def test_persistent_registration_keeps_all_legacy_button_handlers(self):
        view = subscriptions.SubscriptionView(register_all=True)
        self.assertEqual([child.custom_id for child in view.children], [
            "herald_subscribe:free-games", "herald_subscribe:gpu-updates",
            "herald_subscribe:stream-alerts", "herald_subscribe:security-alerts",
            "herald_subscribe:show_mine",
        ])
        self.assertTrue(view.is_persistent())


if __name__ == "__main__":
    unittest.main()
