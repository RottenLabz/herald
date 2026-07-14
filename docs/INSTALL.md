# Herald Angel Installation Guide

This guide describes a basic self-hosted install of Herald Angel.

## Supported install target

These instructions are written for Ubuntu/Debian-style Linux.

Recommended hosting:

- A small VPS.
- An always-on Linux server.
- A homelab machine that stays online.

Herald Angel can be run manually for testing, but for normal use it is best hosted 24/7 so feed checks, Twitch alerts, welcome messages, and subscription buttons remain available.

Windows and macOS may work with manual changes, but they are not the primary documented install targets yet.

## 1. Create a Discord bot

In the Discord Developer Portal:

1. Create an application.
2. Create a bot user.
3. Copy the bot token.
4. Enable these privileged gateway intents:
   - Server Members Intent
   - Message Content Intent

Keep the token private. Do not commit it to Git.

## 2. Invite the bot to your server

Suggested permissions:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Manage Roles, if using subscription buttons
- Use External Emojis, optional
- Attach Files, optional

Keep the Herald Angel bot role below admin, mod, and staff roles.

## 3. Clone and install

    git clone <your-repo-url> herald-angel
    cd herald-angel

    python3 -m venv .venv
    source .venv/bin/activate

    pip install -r requirements.txt

## 4. Configure .env

    cp .env.example .env
    nano .env

Required settings:

    DISCORD_TOKEN=your_discord_bot_token
    OWNER_ID=your_discord_user_id

For a single-server install, the default is sufficient:

    HERALD_GUILD_ID=0

If the bot is connected to more than one Discord server, set this to the ID of
the one server Herald should use. Herald will otherwise refuse ambiguous alert
delivery rather than choosing a server by list order.

Recommended first-run safety settings:

    HERALD_DM_COMMANDS_ENABLED=true
    HERALD_OWNER_ONLY=true
    HERALD_REPLY_TO_NON_OWNER_DMS=false
    HERALD_SERVER_COMMANDS_ENABLED=false
    HERALD_STARTUP_BACKLOG_MODE=held

Recommended module defaults for a gaming-focused install:

    FREE_GAMES_ENABLED=true
    GPU_UPDATES_ENABLED=true
    TWITCH_ENABLED=false
    SECURITY_ENABLED=false

## 5. Create Discord channels

Core/default channel names:

    #welcome
    #subscriptions
    #free-games
    #gpu-updates

Create optional channels only for modules you enable:

    #stream-alerts
    #security-alerts

You can change all channel names in .env.

## 6. Optional subscription roles

If you use the subscription panel, create roles for the modules you enable:

    Free Games
    GPU Updates
    Stream Alerts      # only when Twitch is enabled
    Security Alerts    # only when security is enabled

Only use harmless notification/viewer roles.

Do not map subscription buttons to admin, mod, staff, or privileged roles.

## 7. Run manually

    source .venv/bin/activate
    python bot.py

Then DM the bot:

    herald status
    herald welcome test
    herald runtime
    herald help

## 8. systemd service

A sample service file is included at:

    systemd/herald-angel.service.example

The sample is hardened for an installation under /opt/herald-angel using a dedicated herald service account. Ensure the configured WorkingDirectory, ExecStart, ReadWritePaths, user, group, and writable data/log/backup directories all exist and match your deployment. ProtectHome=true will deliberately block a project kept under /home unless you redesign the unit.

Copy it to /etc/systemd/system/herald-angel.service, review every path/user value, then run:

    sudo systemctl daemon-reload
    sudo systemctl enable herald-angel
    sudo systemctl start herald-angel
    sudo systemctl status herald-angel --no-pager

## 9. Test commands

DM the bot:

    herald status
    herald welcome test
    herald runtime
    herald discover
    herald watch status
    herald audit verify

To post the subscription panel:

    herald subs panel

## 10. Updating

Stop the service, pull changes, update dependencies if needed, then restart.

    cd ~/herald-angel
    sudo systemctl stop herald-angel

    git pull

    source .venv/bin/activate
    pip install -r requirements.txt

    python -m py_compile config.py subscriptions.py bot.py storage.py watchers.py providers/twitch.py providers/gamerpower.py providers/guru3d.py providers/security.py
    python -m unittest discover -s tests -v

    sudo systemctl start herald-angel
    sudo systemctl status herald-angel --no-pager
