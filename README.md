# Herald Angel

Herald Angel is a self-hosted Discord announcement and watcher bot.

It can welcome new members, watch public feeds/APIs, queue announcements, post alerts into configured Discord channels, and let members opt into alert roles using subscription buttons.

Herald Angel is intended for self-hosting. It is not currently offered as a hosted public invite bot or SaaS service.

## Features

- Welcome messages for new Discord members.
- Free game alerts via GamerPower API/RSS.
- GPU driver/update alerts via Guru3D RSS.
- Twitch live alerts via Twitch Helix.
- Security alerts via RSS feeds.
- Subscription role panel with Discord buttons.
- Owner-only DM command controls.
- Outbox-style item queue with held, pending, posted, failed, and skipped states.
- SQLite local storage.
- Local append-only-style audit trail with hash-chain verification.
- Public-safe default configuration.
- NOENV backup helper script.

## Safety defaults

Herald Angel is designed with conservative defaults for public self-hosting:

- Owner-only commands are enabled by default.
- DM commands are enabled by default.
- Server-channel text commands are disabled by default.
- Real secrets live in .env, which should not be committed.
- Provider-controlled text is sanitised before posting to Discord.
- Feed/API posts do not allow @everyone, user mentions, or arbitrary role mentions.
- Twitch role pings are limited to the configured stream-alert role.

## Requirements

- Python 3.11 or newer recommended.
- A Discord bot token.
- A Discord server where you can invite/manage the bot.
- SQLite, included with Python on most systems.
- Optional: Twitch developer app credentials for Twitch live alerts.

## Recommended hosting

Herald Angel is designed to run as an always-on self-hosted bot.

The recommended setup is a small Linux VPS or another always-on Linux machine, so Herald can keep watching feeds and posting alerts 24/7.

The current installation docs are written for Ubuntu/Debian-style Linux using Python virtual environments and systemd.

Other platforms may work, but Windows and macOS are not the primary documented deployment targets yet.

## Quick start

Clone the repo, create a virtual environment, install dependencies, copy the example config, edit .env, and run the bot.

    git clone <your-repo-url> herald-angel
    cd herald-angel

    python3 -m venv .venv
    source .venv/bin/activate

    pip install -r requirements.txt

    cp .env.example .env
    nano .env

    python bot.py

For a more complete setup, see:

- docs/INSTALL.md
- docs/CONFIGURATION.md
- docs/SECURITY.md

## Discord setup overview

Your Discord bot will need these intents enabled in the Discord Developer Portal:

- Server Members Intent
- Message Content Intent

Suggested bot permissions:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Manage Roles, only if using subscription buttons
- Use External Emojis, optional
- Attach Files, optional

Keep Herald Angel's bot role below admin/mod/staff roles.

## Common owner commands

Commands are normally sent to Herald Angel by DM.

Default command prefix:

    herald

Useful commands:

    herald status
    herald welcome test
    herald runtime
    herald subs panel
    herald watch status
    herald discover
    herald run once
    herald deliver pending
    herald held
    herald pending
    herald posted
    herald failed
    herald post <id>
    herald post held <number>
    herald promote <id>
    herald skip <id>
    herald retry failed
    herald audit verify
    herald audit recent [number]
    herald audit summary
    herald audit item <id>
    herald clean [number]
    herald help

## Configuration

Herald Angel reads configuration from .env in the project root.

Important settings include:

    DISCORD_TOKEN=
    OWNER_ID=

    HERALD_DM_COMMANDS_ENABLED=true
    HERALD_OWNER_ONLY=true
    HERALD_SERVER_COMMANDS_ENABLED=false

    FREE_GAMES_CHANNEL_NAME=free-games
    GPU_UPDATES_CHANNEL_NAME=gpu-updates
    STREAM_ALERTS_CHANNEL_NAME=stream-alerts
    SECURITY_ALERTS_CHANNEL_NAME=security-alerts
    WELCOME_CHANNEL_NAME=welcome
    SUBSCRIPTIONS_CHANNEL_NAME=subscriptions

See docs/CONFIGURATION.md for the full configuration guide.

## Backups

The included helper creates a public-safe source backup that excludes secrets, runtime data, virtual environments, Git history, logs, caches, databases, and previous backups.

    chmod +x backup-noenv.sh
    ./backup-noenv.sh

Do not commit generated backup archives.

## Security notes

Do not publish:

- .env
- Discord tokens
- Twitch client secrets
- SQLite runtime databases
- logs
- .venv
- generated backup archives

The local audit trail is useful for operational visibility, but it is not tamper-proof against an administrator with filesystem/database access.

See docs/SECURITY.md.

## Acknowledgements and data sources

Herald Angel can integrate with public APIs/RSS feeds and Discord services, including:

- Discord, via the Discord API and discord.py.
- GamerPower, for free game giveaway data.
- Guru3D, for GPU driver/update RSS items.
- Twitch, via Twitch Helix, when Twitch alerts are enabled.
- Public security RSS feeds such as CISA, BleepingComputer, and UK NCSC, depending on configuration.

Herald Angel is an independent self-hosted project and is not affiliated with, endorsed by, or sponsored by Discord, GamerPower, Guru3D, Twitch, CISA, BleepingComputer, UK NCSC, or any other feed/source provider.

## Licence

MIT License. See LICENSE.
