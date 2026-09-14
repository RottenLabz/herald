# Install RottenLabz Herald

These instructions describe a **new, operator-approved Linux installation**, not an in-place upgrade of an existing private service. Existing deployments must first use [PRIVATE_VPS_MIGRATION_NOTES.md](../PRIVATE_VPS_MIGRATION_NOTES.md). Decide and approve the new service account and directories before creating them. Reuse existing suitable paths where practical.

Use a supported Linux release, Python 3.12 or newer, Git, and a supported real `discord.py` 2.x release. Complete the dependency release gates before production use. A constrained installation that cannot resolve must stop; do not relax the security floors to force it through.

## Discord application

In the Discord Developer Portal, create/select your application and bot; retain the token privately. Invite it with the `bot` and `applications.commands` scopes. Grant View Channel, Send Messages, Embed Links and Read Message History in destination channels. Grant Manage Roles only when subscription controls are used. Place the bot role above harmless notification roles and below staff roles; do not grant Administrator.

Enable the Members intent for member welcomes/subscription member handling and Message Content intent for the retained text/DM transport as required by your application settings. Run `/herald doctor` in the intended guild after login to check actual channel, role and command-registration state.

## Source and account preparation

Obtain the reviewed source from the authoritative GitHub project or a verified source archive. Select the real repository URL yourself; this guide does not invent a remote. Deploy the selected, tested commit into `/opt/rottenlabz-herald`. Root or a separate deployer must own that source and the virtual environment; the `herald` process must not be able to modify them.

**VPS, administrator shell — new-install example only:**

```bash
sudo useradd --system --user-group --home-dir /var/lib/rottenlabz-herald --no-create-home --shell /usr/sbin/nologin herald
sudo install -d -o root -g root -m 0755 /opt/rottenlabz-herald
sudo install -d -o root -g root -m 0700 /etc/rottenlabz-herald
sudo install -d -o herald -g herald -m 0700 /var/lib/rottenlabz-herald
```

Skip account creation if the dedicated account already exists and verify its purpose/ownership. Do not recursively change ownership of an existing private checkout without assessing it.

## Dependencies

Resolve and test in the clean release environment described in [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md). Once the source has been deployed, install the approved hash-locked runtime requirements into `/opt/rottenlabz-herald/.venv`. The candidate contains version ranges and audited minimum constraints, **not a validated exact release lock**. Never use the system Python package environment as the application environment.

For candidate qualification only, from the existing reviewed source checkout:

**Linux test console:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
```

A real release also needs the dependency audit, inventory, SBOM and connected Discord tests in the checklist. Review/create `.venv` only in the designated test/deployment workspace.

## Protect `.env` before adding secrets

Run the following only when the destination environment file does **not** already exist. The `install` command creates it with restrictive ownership/mode before any real credential is added.

**VPS, administrator shell:**

```bash
cd /opt/rottenlabz-herald
sudo test ! -e /etc/rottenlabz-herald/.env
sudo install -o root -g root -m 0600 .env.example /etc/rottenlabz-herald/.env
sudoedit /etc/rottenlabz-herald/.env
```

Run each line separately and stop if the existence check fails. Use your configured editor (including VS Code through a suitable administrative editing workflow) to set the token and IDs. Never paste credentials into terminal commands or print the file for verification. The root systemd manager reads `EnvironmentFile`; the service user need not read the file directly.

Set at least:

```dotenv
DISCORD_TOKEN=
OWNER_ID=0
HERALD_GUILD_ID=0
HERALD_DB_PATH=/var/lib/rottenlabz-herald/herald.db
HERALD_FEED_CONFIG_PATH=/var/lib/rottenlabz-herald/feeds.json
FREE_GAMES_CHANNEL_ID=0
```

Replace `OWNER_ID`, `HERALD_GUILD_ID`, and the destination ID with your actual IDs before starting. Use a real nonzero target guild ID for slash registration and deliberate server targeting. Set `HERALD_BUILD_ID` to the reviewed deployment commit without shelling out from the bot. Leave `HERALD_PRIVATE_PROVIDERS=[]` unless trusted private modules have been separately reviewed.

**VPS, administrator shell — metadata checks without displaying values:**

```bash
sudo stat -c '%a %U:%G %n' /etc/rottenlabz-herald /etc/rottenlabz-herald/.env
sudo -u herald test ! -w /opt/rottenlabz-herald/bot.py
sudo -u herald test -w /var/lib/rottenlabz-herald
```

Expected modes are `700 root:root` for the configuration directory and `600 root:root` for `.env`. In a local development checkout use `install -m 0600 .env.example .env` **only for a new file**, then edit it. Do not copy secrets into a permissively created file and fix permissions afterward.

## Service

Review every path and use [systemd/rottenlabz-herald.service.example](../systemd/rottenlabz-herald.service.example). `ProtectHome=true` intentionally prevents the `/home` private deployment from working unchanged. `ProtectSystem=strict` allows application writes only beneath the configured runtime directory; logs go to the journal.

**VPS, administrator shell:**

```bash
sudo install -o root -g root -m 0644 systemd/rottenlabz-herald.service.example /etc/systemd/system/rottenlabz-herald.service
sudo systemd-analyze verify /etc/systemd/system/rottenlabz-herald.service
sudo systemctl daemon-reload
sudo systemctl enable --now rottenlabz-herald.service
sudo systemctl status rottenlabz-herald.service --no-pager
```

Only run enable/start after configuration, permissions and dependency checks pass. Verify login, `/herald doctor`, owner/non-owner authorization, harmless subscriptions, preview, and one controlled post in a test channel. Then verify audit state and restart recovery. This build did not carry out those live actions.

## Updating

Stop the service before replacing application files or migrating its database. Back up consistent private state and reviewed source first. Deploy the reviewed commit and approved runtime dependencies using the deployer account. Preserve `/etc` and `/var/lib` data. Run tests and a copy-of-database migration/integrity/audit check before starting the upgraded service. Rollback requires the matching pre-migration database plus old source/environment, not merely an old Python file.
