import unittest
from unittest.mock import patch

import subscriptions


class SubscriptionModuleTests(unittest.TestCase):
    def test_view_only_builds_buttons_for_enabled_modules(self):
        enabled = {
            "free-games": subscriptions.ALL_SUBSCRIPTION_DEFS["free-games"],
            "stream-alerts": subscriptions.ALL_SUBSCRIPTION_DEFS["stream-alerts"],
        }

        with patch.dict(subscriptions.SUBSCRIPTION_DEFS, enabled, clear=True):
            view = subscriptions.SubscriptionView()

        custom_ids = [child.custom_id for child in view.children]
        self.assertEqual(
            custom_ids,
            [
                "herald_subscribe:free-games",
                "herald_subscribe:stream-alerts",
                "herald_subscribe:show_mine",
            ],
        )

    def test_empty_module_set_still_has_status_button(self):
        with patch.dict(subscriptions.SUBSCRIPTION_DEFS, {}, clear=True):
            view = subscriptions.SubscriptionView()

        self.assertEqual(len(view.children), 1)
        self.assertEqual(
            view.children[0].custom_id,
            "herald_subscribe:show_mine",
        )

    def test_persistent_registration_keeps_all_legacy_button_handlers(self):
        view = subscriptions.SubscriptionView(register_all=True)
        custom_ids = [child.custom_id for child in view.children]

        self.assertEqual(
            custom_ids,
            [
                "herald_subscribe:free-games",
                "herald_subscribe:gpu-updates",
                "herald_subscribe:stream-alerts",
                "herald_subscribe:security-alerts",
                "herald_subscribe:show_mine",
            ],
        )


if __name__ == "__main__":
    unittest.main()
