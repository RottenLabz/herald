import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(
    dotenv_path=BASE_DIR / ".env",
    override=True,
    encoding="utf-8-sig",
)


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


DISCORD_TOKEN = env_str("DISCORD_TOKEN")
OWNER_ID = env_int("OWNER_ID", 0)
HERALD_GUILD_ID = env_int("HERALD_GUILD_ID", 0)

HERALD_NAME = env_str("HERALD_NAME", "Herald Angel")
HERALD_COMMAND_PREFIX = env_str("HERALD_COMMAND_PREFIX", "herald").lower()

HERALD_DM_COMMANDS_ENABLED = env_bool("HERALD_DM_COMMANDS_ENABLED", True)
HERALD_OWNER_ONLY = env_bool("HERALD_OWNER_ONLY", True)
HERALD_REPLY_TO_NON_OWNER_DMS = env_bool("HERALD_REPLY_TO_NON_OWNER_DMS", False)
HERALD_SERVER_COMMANDS_ENABLED = env_bool("HERALD_SERVER_COMMANDS_ENABLED", False)

WELCOME_ENABLED = env_bool("WELCOME_ENABLED", True)
WELCOME_CHANNEL_NAME = env_str("WELCOME_CHANNEL_NAME", "welcome")
SUBSCRIPTIONS_CHANNEL_NAME = env_str("SUBSCRIPTIONS_CHANNEL_NAME", "subscriptions")

FREE_GAMES_CHANNEL_NAME = env_str("FREE_GAMES_CHANNEL_NAME", "free-games")
GPU_UPDATES_CHANNEL_NAME = env_str("GPU_UPDATES_CHANNEL_NAME", "gpu-updates")
STREAM_ALERTS_CHANNEL_NAME = env_str("STREAM_ALERTS_CHANNEL_NAME", "stream-alerts")
SECURITY_ALERTS_CHANNEL_NAME = env_str("SECURITY_ALERTS_CHANNEL_NAME", "security-alerts")

HERALD_DB_PATH = env_str("HERALD_DB_PATH", "./data/herald.db")

# Server emoji / presentation.
# These are safe to keep in source: they are public Discord emoji IDs, not secrets.
HERALD_EMOJIS = {
    "herald": env_str("HERALD_EMOJI_HERALD", "🎺"),
    "free_game": env_str("HERALD_EMOJI_FREE_GAME", "🎮"),
    "gcard": env_str("HERALD_EMOJI_GCARD", "🖥️"),
    "security": env_str("HERALD_EMOJI_SECURITY", "🛡️"),
}

# Watcher settings
HERALD_AUTO_POST_ENABLED = env_bool("HERALD_AUTO_POST_ENABLED", True)
HERALD_CHECK_SECONDS = max(60, min(env_int("HERALD_CHECK_SECONDS", 3600), 86400))
HERALD_STARTUP_BACKLOG_MODE = env_str("HERALD_STARTUP_BACKLOG_MODE", "held").lower()
HERALD_POST_BATCH_LIMIT = max(1, min(env_int("HERALD_POST_BATCH_LIMIT", 10), 100))
HERALD_DELIVERY_MAX_ATTEMPTS = max(1, min(env_int("HERALD_DELIVERY_MAX_ATTEMPTS", 5), 20))

# Provider modules. Gaming modules stay enabled by default. Security remains
# enabled for backwards compatibility with v0.1.0 installs, while the new
# public .env.example disables it so fresh gaming-focused installs can opt in.
FREE_GAMES_ENABLED = env_bool("FREE_GAMES_ENABLED", True)
GPU_UPDATES_ENABLED = env_bool("GPU_UPDATES_ENABLED", True)
SECURITY_ENABLED = env_bool("SECURITY_ENABLED", True)

GAMERPOWER_API_URL = env_str(
    "GAMERPOWER_API_URL",
    "https://www.gamerpower.com/api/giveaways",
)
GAMERPOWER_RSS_URL = env_str(
    "GAMERPOWER_RSS_URL",
    "https://www.gamerpower.com/rss/giveaways",
)
GURU3D_RSS_URL = env_str(
    "GURU3D_RSS_URL",
    "https://www.guru3d.com/rss.xml",
)

SECURITY_RSS_URLS = [
    url.strip()
    for url in env_str(
        "SECURITY_RSS_URLS",
        (
            "https://www.cisa.gov/cybersecurity-advisories/all.xml,"
            "https://www.bleepingcomputer.com/feed/,"
            "https://www.ncsc.gov.uk/api/1/services/v1/all-rss-feed.xml"
        ),
    ).split(",")
    if url.strip()
]

TWITCH_ENABLED = env_bool("TWITCH_ENABLED", False)
TWITCH_CLIENT_ID = env_str("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = env_str("TWITCH_CLIENT_SECRET")
TWITCH_CHANNELS = [
    channel.strip().lower()
    for channel in env_str("TWITCH_CHANNELS", "").split(",")
    if channel.strip()
]
TWITCH_TOKEN_URL = env_str(
    "TWITCH_TOKEN_URL",
    "https://id.twitch.tv/oauth2/token",
)
TWITCH_STREAMS_URL = env_str(
    "TWITCH_STREAMS_URL",
    "https://api.twitch.tv/helix/streams",
)
TWITCH_USER_AGENT = env_str(
    "TWITCH_USER_AGENT",
    "Herald Angel Twitch Watcher",
)
TWITCH_PING_ROLE_ENABLED = env_bool("TWITCH_PING_ROLE_ENABLED", True)
TWITCH_PING_ROLE_NAME = env_str("TWITCH_PING_ROLE_NAME", "Stream Alerts")

HERALD_FREE_GAME_STRICT_FILTER = env_bool("HERALD_FREE_GAME_STRICT_FILTER", True)
HERALD_SECURITY_STRICT_FILTER = env_bool("HERALD_SECURITY_STRICT_FILTER", True)
