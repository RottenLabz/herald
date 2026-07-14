# Herald Angel Configuration

Herald Angel reads configuration from .env in the project root.

Never commit your real .env.

## Required settings

    DISCORD_TOKEN=
    OWNER_ID=0

DISCORD_TOKEN is your Discord bot token.

OWNER_ID is your Discord user ID. Owner-only commands are checked against this ID.

## Discord server target

    HERALD_GUILD_ID=0

For a normal one-server install, leave this at `0` and Herald selects the only
server it is connected to.

If the bot is connected to more than one server, set `HERALD_GUILD_ID` to the
server ID Herald is allowed to use. Without an explicit ID, Herald fails closed
instead of guessing which server should receive alerts.

## Bot identity

    HERALD_NAME=Herald Angel
    HERALD_COMMAND_PREFIX=herald

## Command safety

Recommended public/self-host defaults:

    HERALD_DM_COMMANDS_ENABLED=true
    HERALD_OWNER_ONLY=true
    HERALD_REPLY_TO_NON_OWNER_DMS=false
    HERALD_SERVER_COMMANDS_ENABLED=false

Meaning:

- Owner DM commands work.
- Non-owner DMs are ignored by default.
- Server-channel text commands are disabled by default.
- The bot is controlled through owner DMs.

## Channels

    WELCOME_CHANNEL_NAME=welcome
    SUBSCRIPTIONS_CHANNEL_NAME=subscriptions
    FREE_GAMES_CHANNEL_NAME=free-games
    GPU_UPDATES_CHANNEL_NAME=gpu-updates
    STREAM_ALERTS_CHANNEL_NAME=stream-alerts
    SECURITY_ALERTS_CHANNEL_NAME=security-alerts

The bot finds text channels by name.

## Database

    HERALD_DB_PATH=./data/herald.db

The database stores queue items, events, and audit records.

Do not commit the database.

## Emoji settings

Unicode defaults:

    HERALD_EMOJI_HERALD=🎺
    HERALD_EMOJI_FREE_GAME=🎮
    HERALD_EMOJI_GCARD=🖥️
    HERALD_EMOJI_SECURITY=🛡️

You may override these with custom Discord emoji in your private .env.

## Watcher settings

    HERALD_AUTO_POST_ENABLED=true
    HERALD_CHECK_SECONDS=3600
    HERALD_STARTUP_BACKLOG_MODE=held
    HERALD_POST_BATCH_LIMIT=10
    HERALD_DELIVERY_MAX_ATTEMPTS=5

HERALD_CHECK_SECONDS controls how often Herald checks providers.

HERALD_DELIVERY_MAX_ATTEMPTS controls automatic retries for failed Discord
deliveries. Failed items are requeued on later watcher cycles until they reach
the configured cap. Pending delivery is first-in, first-out so older items are
not starved by newer discoveries. The owner command `herald retry failed` is
a deliberate manual override and can requeue items that reached the automatic cap.

The recommended public/self-host default is 3600 seconds, or once per hour. This is conservative and avoids unnecessary load on public feeds and APIs.

If you change this value, choose an interval that is reasonable for the providers you use and respect any rate limits or usage guidance from those services.

## Provider modules

    FREE_GAMES_ENABLED=true
    GPU_UPDATES_ENABLED=true
    TWITCH_ENABLED=false
    SECURITY_ENABLED=false

Only enabled providers are fetched and shown in the subscription panel.

Gaming modules are enabled in the public example. Twitch and security are optional for fresh installs. For backwards compatibility, an upgraded v0.1.0 install that does not yet define SECURITY_ENABLED keeps its previous enabled security behaviour; add SECURITY_ENABLED=false explicitly to disable it.

## GamerPower settings

    GAMERPOWER_API_URL=https://www.gamerpower.com/api/giveaways
    GAMERPOWER_RSS_URL=https://www.gamerpower.com/rss/giveaways
    HERALD_FREE_GAME_STRICT_FILTER=true

The provider uses the API first and falls back to RSS.

Strict filtering removes common mobile/noisy giveaway items.

## Guru3D GPU update settings

    GURU3D_RSS_URL=https://www.guru3d.com/rss.xml

The provider filters RSS items for GPU/driver-related posts.

## Security RSS settings

Security is an optional, review-first module. Enable it with:

    SECURITY_ENABLED=true

    SECURITY_RSS_URLS=https://www.cisa.gov/cybersecurity-advisories/all.xml,https://www.bleepingcomputer.com/feed/,https://www.ncsc.gov.uk/api/1/services/v1/all-rss-feed.xml
    HERALD_SECURITY_STRICT_FILTER=true

Security alerts are held for manual review by default.

## Twitch settings

Twitch is an optional module and is disabled by default.

    TWITCH_ENABLED=false
    TWITCH_CLIENT_ID=
    TWITCH_CLIENT_SECRET=
    TWITCH_CHANNELS=
    TWITCH_TOKEN_URL=https://id.twitch.tv/oauth2/token
    TWITCH_STREAMS_URL=https://api.twitch.tv/helix/streams
    TWITCH_USER_AGENT=Herald Angel Twitch Watcher
    TWITCH_PING_ROLE_ENABLED=true
    TWITCH_PING_ROLE_NAME=Stream Alerts

TWITCH_CHANNELS is a comma-separated list of Twitch login names.

Example:

    TWITCH_ENABLED=true
    TWITCH_CLIENT_ID=your_client_id
    TWITCH_CLIENT_SECRET=your_client_secret
    TWITCH_CHANNELS=examplechannel,anotherchannel

If role pings are enabled, Herald only allows the configured Twitch alert role to be pinged.

## Subscription roles

Expected roles for the modules you enable:

    Free Games
    GPU Updates
    Stream Alerts
    Security Alerts

Only use harmless notification/viewer roles.

Do not use admin, mod, staff, or privileged roles.

## Public release notes

Before making a fork/repo public, run checks for secrets and private names.

Example:

    git grep -nE 'DISCORD_TOKEN=.+|TWITCH_CLIENT_SECRET=.+|OWNER_ID=[0-9]{8,}|Bot [A-Za-z0-9._-]{40,}|mfa\.|PRIVATE KEY|BEGIN OPENSSH' || echo "Tracked secret scan passed"
