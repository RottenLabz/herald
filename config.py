"""Configuration for one self-hosted Herald instance."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = Path(os.getenv("HERALD_ENV_PATH", str(BASE_DIR / ".env")))
if ENV_PATH.is_file():
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_PATH, override=False, encoding="utf-8-sig")


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        result = int(value.strip())
    except (ValueError, TypeError):
        raise ValueError(f"Invalid integer configuration: {name}") from None
    if result < 0 or result > 2**63 - 1:
        raise ValueError(f"Integer configuration out of range: {name}")
    return result


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    token = value.strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean configuration: {name}")


DISCORD_TOKEN = env_str("DISCORD_TOKEN")
OWNER_ID = env_int("OWNER_ID")
HERALD_GUILD_ID = env_int("HERALD_GUILD_ID")
HERALD_NAME = env_str("HERALD_NAME", "Herald")
HERALD_COMMAND_PREFIX = env_str("HERALD_COMMAND_PREFIX", "herald").lower()
HERALD_DM_COMMANDS_ENABLED = env_bool("HERALD_DM_COMMANDS_ENABLED", True)
# Deprecated: validate the legacy value but never allow it to weaken ownership.
env_bool("HERALD_OWNER_ONLY", True)
HERALD_OWNER_ONLY = True
HERALD_REPLY_TO_NON_OWNER_DMS = env_bool("HERALD_REPLY_TO_NON_OWNER_DMS", False)
HERALD_SERVER_COMMANDS_ENABLED = env_bool("HERALD_SERVER_COMMANDS_ENABLED", False)
HERALD_DB_PATH = env_str("HERALD_DB_PATH", "./data/herald.db")
HERALD_FEED_CONFIG_PATH = env_str("HERALD_FEED_CONFIG_PATH", "./data/feeds.json")
try:
    HERALD_PRIVATE_PROVIDERS = json.loads(env_str("HERALD_PRIVATE_PROVIDERS", "[]"))
except (ValueError, TypeError):
    raise ValueError("HERALD_PRIVATE_PROVIDERS must be a JSON list") from None
if not isinstance(HERALD_PRIVATE_PROVIDERS, list) or len(HERALD_PRIVATE_PROVIDERS) > 16:
    raise ValueError("HERALD_PRIVATE_PROVIDERS must contain at most 16 entries")

WELCOME_ENABLED = env_bool("WELCOME_ENABLED", True)
WELCOME_CHANNEL_NAME = env_str("WELCOME_CHANNEL_NAME", "welcome")
SUBSCRIPTIONS_CHANNEL_NAME = env_str("SUBSCRIPTIONS_CHANNEL_NAME", "subscriptions")
WELCOME_CHANNEL_ID = env_int("WELCOME_CHANNEL_ID")
SUBSCRIPTIONS_CHANNEL_ID = env_int("SUBSCRIPTIONS_CHANNEL_ID")
HERALD_EMOJIS = {
    "herald": env_str("HERALD_EMOJI_HERALD", "🎺"),
    "free_game": env_str("HERALD_EMOJI_FREE_GAME", "🎮"),
    "gcard": env_str("HERALD_EMOJI_GCARD", "🖥️"),
    "security": env_str("HERALD_EMOJI_SECURITY", "🛡️"),
}
HERALD_AUTO_POST_ENABLED = env_bool("HERALD_AUTO_POST_ENABLED", True)
HERALD_CHECK_SECONDS = max(60, min(env_int("HERALD_CHECK_SECONDS", 3600), 86400))
HERALD_DELIVERY_SECONDS = max(5, min(env_int("HERALD_DELIVERY_SECONDS", 15), 3600))
HERALD_STARTUP_BACKLOG_MODE = env_str("HERALD_STARTUP_BACKLOG_MODE", "held").lower()
if HERALD_STARTUP_BACKLOG_MODE not in {"held", "pending", "skipped"}:
    raise ValueError("HERALD_STARTUP_BACKLOG_MODE must be held, pending or skipped")
HERALD_POST_BATCH_LIMIT = max(1, min(env_int("HERALD_POST_BATCH_LIMIT", 10), 100))
HERALD_DELIVERY_MAX_ATTEMPTS = max(1, min(env_int("HERALD_DELIVERY_MAX_ATTEMPTS", 5), 20))
FREE_GAMES_ENABLED = env_bool("FREE_GAMES_ENABLED", True)
FREE_GAMES_CHANNEL_ID = env_int("FREE_GAMES_CHANNEL_ID")
FREE_GAMES_ROLE_ID = env_int("FREE_GAMES_ROLE_ID")
FREE_GAMES_DELIVERY_MODE = env_str("FREE_GAMES_DELIVERY_MODE", "automatic")
if FREE_GAMES_DELIVERY_MODE not in {"automatic", "review"}:
    raise ValueError("FREE_GAMES_DELIVERY_MODE must be automatic or review")
GAMERPOWER_API_URL = "https://www.gamerpower.com/api/giveaways"
GAMERPOWER_RSS_URL = "https://www.gamerpower.com/rss/giveaways"
HERALD_FREE_GAME_STRICT_FILTER = env_bool("HERALD_FREE_GAME_STRICT_FILTER", True)

HERALD_COMMAND_SCOPE = env_str("HERALD_COMMAND_SCOPE", "guild")
if HERALD_COMMAND_SCOPE not in {"guild", "global"}:
    raise ValueError("HERALD_COMMAND_SCOPE must be guild or global")
HERALD_BUILD_ID = env_str("HERALD_BUILD_ID", "")
# Optional commit/build label, never a free-form credential-bearing value.
import re
if HERALD_BUILD_ID and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", HERALD_BUILD_ID):
    raise ValueError("HERALD_BUILD_ID must be a short alphanumeric build label")
