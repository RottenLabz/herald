import unittest
from dataclasses import dataclass
from unittest.mock import patch

import bot


@dataclass
class FakeGuild:
    id: int
    name: str


class FakeClient:
    def __init__(self, guilds):
        self.guilds = list(guilds)

    def get_guild(self, guild_id):
        for guild in self.guilds:
            if guild.id == guild_id:
                return guild
        return None


class GuildTargetingTests(unittest.TestCase):
    def test_single_guild_is_selected_automatically(self):
        guild = FakeGuild(123, "Only Server")

        with patch.object(bot, "client", FakeClient([guild])), patch.object(
            bot,
            "HERALD_GUILD_ID",
            0,
        ):
            self.assertIs(bot.configured_guild(), guild)

    def test_multiple_guilds_fail_closed_without_explicit_id(self):
        guilds = [FakeGuild(123, "One"), FakeGuild(456, "Two")]

        with patch.object(bot, "client", FakeClient(guilds)), patch.object(
            bot,
            "HERALD_GUILD_ID",
            0,
        ):
            self.assertIsNone(bot.configured_guild())
            self.assertIn("more than one server", bot.configured_guild_error())

    def test_explicit_guild_id_selects_only_intended_server(self):
        guilds = [FakeGuild(123, "One"), FakeGuild(456, "Two")]

        with patch.object(bot, "client", FakeClient(guilds)), patch.object(
            bot,
            "HERALD_GUILD_ID",
            456,
        ):
            self.assertIs(bot.configured_guild(), guilds[1])


if __name__ == "__main__":
    unittest.main()
