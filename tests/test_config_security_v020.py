import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationSecurityTests(unittest.TestCase):
    def run_config(self, values, expression="print(config.HERALD_OWNER_ONLY)"):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HERALD_", "OWNER_", "FREE_GAMES_"))}
        env.update(values)
        env["HERALD_ENV_PATH"] = str(ROOT / "not-created-test-env")
        return subprocess.run([sys.executable, "-c", "import config; " + expression], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=10)

    def test_owner_false_never_disables_ownership(self):
        result = self.run_config({"HERALD_OWNER_ONLY": "false"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "True")

    def test_welcome_emoji_is_independent_of_herald_emoji(self):
        for herald, welcome in (("🎺", "👋"), ("📣", "🌻")):
            with self.subTest(herald=herald, welcome=welcome):
                result = self.run_config(
                    {"HERALD_EMOJI_HERALD": herald, "HERALD_EMOJI_WELCOME": welcome},
                    "print(config.HERALD_EMOJIS['herald']); print(config.HERALD_EMOJIS['welcome'])")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), [herald, welcome])

    def test_welcome_emoji_default_survives_changed_herald_emoji(self):
        result = self.run_config({"HERALD_EMOJI_HERALD": "📣"},
                                 "print(config.HERALD_EMOJIS['welcome'])")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "👋")

    def test_security_booleans_reject_invalid_and_blank(self):
        for name in ("HERALD_OWNER_ONLY", "HERALD_SERVER_COMMANDS_ENABLED", "HERALD_DM_COMMANDS_ENABLED"):
            for value in ("", "treu", "sometimes"):
                with self.subTest(name=name, value=value):
                    result = self.run_config({name: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Invalid boolean configuration", result.stderr)

    def test_explicit_boolean_tokens_and_scope(self):
        for token in ("1", "true", "yes", "on", "0", "false", "no", "off"):
            self.assertEqual(self.run_config({"HERALD_SERVER_COMMANDS_ENABLED": token}).returncode, 0)
        for scope in ("guild", "global"):
            self.assertEqual(self.run_config({"HERALD_COMMAND_SCOPE": scope}).returncode, 0)
        self.assertNotEqual(self.run_config({"HERALD_COMMAND_SCOPE": ""}).returncode, 0)

    def test_invalid_ids_and_build_labels_rejected(self):
        for values in ({"OWNER_ID": "oops"}, {"HERALD_GUILD_ID": "-1"}, {"HERALD_BUILD_ID": "https://secret.invalid/token"}):
            self.assertNotEqual(self.run_config(values).returncode, 0)


if __name__ == "__main__":
    unittest.main()
