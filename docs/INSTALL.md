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

Recommended first-run safety settings:

    HERALD_DM_COMMANDS_ENABLED=true
    HERALD_OWNER_ONLY=true
    HERALD_REPLY_TO_NON_OWNER_DMS=false
    HERALD_SERVER_COMMANDS_ENABLED=false
    HERALD_STARTUP_BACKLOG_MODE=held

## 5. Create Discord channels

Default channel names:

    #welcome
    #subscriptions
    #free-games
    #gpu-updates
    #stream-alerts
    #security-alerts

You can change these names in .env.

## 6. Optional subscription roles

If you use the subscription panel, create these roles by default:

    Free Games
    GPU Updates
    Stream Alerts
    Security Alerts

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

Copy it to /etc/systemd/system/herald-angel.service, edit the paths/user, then run:

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

    sudo systemctl start herald-angel
    sudo systemctl status herald-angel --no-pager
