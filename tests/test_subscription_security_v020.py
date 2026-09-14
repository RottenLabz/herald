import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import discord
import subscriptions


class Role:
    def __init__(self, guild, role_id, *, name="News", position=1, permissions=None, managed=False):
        self.guild = guild
        self.id = role_id
        self.name = name
        self.position = position
        self.permissions = permissions or discord.Permissions.none()
        self.managed = managed

    def __ge__(self, other):
        return self.position >= other.position

    def is_default(self):
        return self.id == self.guild.id


def source(source_id="news", role_id=20, **overrides):
    return {"id": source_id, "name": "Project News", "enabled": True, "channel_id": 30, "role_id": role_id, **overrides}


class SubscriptionSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sources = [source()]
        subscriptions.configure_subscriptions(42, lambda: self.sources)
        self.guild = SimpleNamespace(id=42)
        self.role = Role(self.guild, 20)
        self.channel = SimpleNamespace(
            id=30, guild=self.guild,
            overwrites_for=lambda role: discord.PermissionOverwrite(),
        )
        self.guild.channels = [self.channel]
        self.guild.get_role = lambda role_id: self.role if role_id == self.role.id else None
        self.guild.get_channel = lambda channel_id: self.channel if channel_id == 30 else None
        self.guild.me = SimpleNamespace(
            guild_permissions=discord.Permissions(manage_roles=True),
            top_role=Role(self.guild, 90, position=10),
        )
        self.member = MagicMock(spec=discord.Member)
        self.member.guild = self.guild
        self.member.roles = []
        self.member.add_roles = AsyncMock()
        self.member.remove_roles = AsyncMock()
        self.interaction = SimpleNamespace(
            guild=self.guild, user=self.member,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def tearDown(self):
        subscriptions.configure_subscriptions(0, ())

    async def choose(self, value=None, view=None):
        view = view or subscriptions.SubscriptionView()
        select = view.children[0]
        token = value or subscriptions.get_subscription_definitions()[0].token
        with patch.object(type(select), "values", new_callable=PropertyMock, return_value=[token]):
            await select.callback(self.interaction)

    def no_role_changes(self):
        self.member.add_roles.assert_not_awaited()
        self.member.remove_roles.assert_not_awaited()

    async def test_target_guild_member_can_toggle_only_configured_safe_role(self):
        await self.choose()
        self.member.add_roles.assert_awaited_once_with(self.role, reason="Herald subscription opt-in")
        self.member.roles = [self.role]
        await self.choose()
        self.member.remove_roles.assert_awaited_once_with(self.role, reason="Herald subscription opt-out")
        mentions = self.interaction.response.send_message.call_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)
        self.assertTrue(self.interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_select_wrong_guild_fails_before_role_lookup(self):
        self.interaction.guild = SimpleNamespace(id=99)
        await self.choose()
        self.no_role_changes()
        self.assertIn("configured server", self.interaction.response.send_message.call_args.args[0])

    async def test_legacy_buttons_in_wrong_guild_never_mutate_roles(self):
        self.interaction.guild = SimpleNamespace(id=99)
        for child in subscriptions.SubscriptionView(register_all=True).children:
            await child.callback(self.interaction)
        self.no_role_changes()
        self.assertEqual(self.interaction.response.send_message.await_count, 5)

    async def test_legacy_buttons_in_target_guild_are_helpful_noops(self):
        for child in subscriptions.SubscriptionView(register_all=True).children:
            await child.callback(self.interaction)
        self.no_role_changes()
        for call in self.interaction.response.send_message.call_args_list:
            self.assertIn("retired", call.args[0])

    async def test_dm_or_mismatched_member_guild_fails_closed(self):
        self.interaction.guild = None
        await self.choose()
        self.interaction.guild = self.guild
        self.member.guild = SimpleNamespace(id=99)
        await self.choose()
        self.no_role_changes()

    async def test_unconfigured_guild_fails_closed(self):
        subscriptions.configure_subscriptions(0, self.sources)
        await self.choose()
        self.no_role_changes()

    async def test_bot_requires_manage_roles(self):
        self.guild.me.guild_permissions = discord.Permissions.none()
        await self.choose()
        self.no_role_changes()
        self.assertIn("Manage Roles", self.interaction.response.send_message.call_args.args[0])

    async def test_equal_and_higher_roles_cannot_be_granted(self):
        for position in (10, 11):
            with self.subTest(position=position):
                self.role.position = position
                await self.choose()
                self.no_role_changes()

    async def test_default_and_managed_roles_cannot_be_granted(self):
        self.role.managed = True
        await self.choose()
        self.role.managed = False
        self.role.id = 42
        self.sources[0]["role_id"] = 42
        await self.choose()
        self.no_role_changes()

    async def test_all_recognized_dangerous_role_permissions_are_rejected(self):
        for permission in sorted(subscriptions.DANGEROUS_PERMISSIONS):
            if permission not in discord.Permissions.VALID_FLAGS:
                continue
            with self.subTest(permission=permission):
                self.role.permissions = discord.Permissions(**{permission: True})
                await self.choose()
                self.no_role_changes()

    async def test_unknown_future_permission_bit_is_rejected(self):
        self.role.permissions = discord.Permissions(1 << 63)
        await self.choose()
        self.no_role_changes()

    async def test_privileged_channel_overwrite_is_rejected(self):
        self.channel.overwrites_for = lambda role: discord.PermissionOverwrite(manage_messages=True)
        await self.choose()
        self.no_role_changes()
        self.assertIn("channel permissions", self.interaction.response.send_message.call_args.args[0])

    async def test_staff_role_name_is_rejected_even_without_permission_bits(self):
        self.role.name = "Community Staff"
        await self.choose()
        self.no_role_changes()

    async def test_missing_role_or_channel_is_rejected(self):
        original = self.guild.get_role
        self.guild.get_role = lambda role_id: None
        await self.choose()
        self.guild.get_role = original
        self.guild.get_channel = lambda channel_id: None
        await self.choose()
        self.no_role_changes()

    async def test_stale_panel_cannot_grant_reconfigured_role(self):
        view = subscriptions.SubscriptionView()
        old_token = subscriptions.get_subscription_definitions()[0].token
        self.sources[0]["role_id"] = 21
        self.role.id = 21
        await self.choose(old_token, view)
        self.no_role_changes()
        self.assertIn("changed or is disabled", self.interaction.response.send_message.call_args.args[0])

    async def test_disabled_source_is_rechecked_on_existing_panel(self):
        view = subscriptions.SubscriptionView()
        old_token = subscriptions.get_subscription_definitions()[0].token
        self.sources[0]["enabled"] = False
        await self.choose(old_token, view)
        self.no_role_changes()

    async def test_configuration_ambiguity_blocks_old_panel(self):
        view = subscriptions.SubscriptionView()
        token = subscriptions.get_subscription_definitions()[0].token
        self.sources.append(source("other", 20))
        await self.choose(token, view)
        self.no_role_changes()

    async def test_unconfigured_token_cannot_mutate_roles(self):
        await self.choose("forged-notification-role")
        self.no_role_changes()

    async def test_display_text_cannot_inject_markdown_or_mentions(self):
        self.sources[0]["name"] = "[Admin](https://example.com) @everyone"
        await self.choose()
        response = self.interaction.response.send_message.call_args.args[0]
        self.assertIn(r"\[Admin\]", response)
        self.assertNotIn("@everyone", response)

    async def test_diagnostics_are_sanitized_when_supplier_raises(self):
        def broken():
            raise RuntimeError("https://" + "user:SECRET@example.invalid /private/provider.py")
        subscriptions.configure_subscriptions(42, broken)
        findings = subscriptions.subscription_diagnostics(self.guild)
        self.assertEqual(findings[0]["status"], "FAIL")
        self.assertNotIn("SECRET", str(findings))
        self.assertNotIn("provider.py", str(findings))

    async def test_diagnostics_report_missing_and_dangerous_roles(self):
        self.role.permissions = discord.Permissions(administrator=True)
        findings = subscriptions.subscription_diagnostics(self.guild)
        self.assertEqual(findings[0]["status"], "FAIL")
        self.assertIn("Privileged", findings[0]["detail"])

    async def test_select_pages_respect_25_option_limit_and_persist(self):
        self.sources[:] = [source(f"source-{index}", 100 + index) for index in range(51)]
        views = subscriptions.subscription_views()
        self.assertEqual([len(view.children[0].options) for view in views], [25, 25, 1])
        self.assertEqual(len({view.children[0].custom_id for view in views}), 3)
        self.assertTrue(all(view.is_persistent() for view in views))
        embed = subscriptions.make_subscription_embed(self.guild, page=2)
        self.assertEqual(len(embed.fields), 1)

    async def test_empty_registration_pages_have_all_stable_select_handlers(self):
        self.sources.clear()
        views = [subscriptions.SubscriptionView(page=page, register_empty=True) for page in range(11)]
        selectors = [view.children[0] for view in views]
        self.assertEqual([select.custom_id for select in selectors], [
            f"herald_subscribe:v020:select:{page}" for page in range(11)
        ])
        self.assertTrue(all(select.disabled for select in selectors))
        self.assertTrue(all(len(select.options) == 1 for select in selectors))
        self.assertTrue(all(view.is_persistent() for view in views))
        # An existing message may become eligible again after a runtime re-enable.
        self.sources.append(source())
        await self.choose(view=views[0])
        self.member.add_roles.assert_awaited_once()

    async def test_empty_registration_sentinel_never_changes_roles(self):
        view = subscriptions.SubscriptionView(page=10, register_empty=True)
        await self.choose("unavailable", view)
        self.no_role_changes()

    async def test_missing_and_duplicate_configuration_is_rejected(self):
        invalid = [
            [source(), source()],
            [source(), source("other", 20)],
            [source(role_id="20")], [source(role_id=True)],
            [source(channel_id=0)], [source(enabled="false")],
            [source(name="")], [source(source_id="")],
        ]
        for sources in invalid:
            with self.subTest(sources=sources):
                subscriptions.configure_subscriptions(42, sources)
                with self.assertRaises(subscriptions.SubscriptionConfigurationError):
                    subscriptions.get_subscription_definitions()

    async def test_optional_role_and_disabled_sources_produce_no_options(self):
        self.sources[:] = [source(role_id=None), source("disabled", enabled=False)]
        self.assertEqual(subscriptions.get_subscription_definitions(), ())
        self.assertEqual(len(subscriptions.SubscriptionView().children), 1)


if __name__ == "__main__":
    unittest.main()
