# Install RottenLabz Herald

These instructions describe a **new Linux installation**. Existing deployments should make verified backups and review configuration/database migration requirements before upgrading in place. Decide the service account and directories before creating them, and reuse suitable existing paths where practical.

The v1.0 series deployment target is a supported Linux x86_64 release with **Python 3.12**, Git, and the hash-locked runtime shipped with this source. Each maintenance release must requalify that exact closure before publication. Newer Python versions or different platform closures must be qualified separately. A constrained installation that cannot install the reviewed lock must stop; do not relax hashes or security floors to force it through.

## Discord application

In the Discord Developer Portal, create/select your application and bot; retain the token privately. Invite it with the `bot` and `applications.commands` scopes. Grant View Channel, Send Messages, Embed Links and Read Message History in destination channels. Grant Manage Roles only when subscription controls are used. Place the bot role above harmless notification roles and below staff roles; do not grant Administrator.

Enable the Members intent for member welcomes/subscription member handling. The privileged Message Content intent is needed only if you deliberately enable legacy target-guild text commands with `HERALD_SERVER_COMMANDS_ENABLED=true`; slash commands and the owner DM recovery path work with that privileged intent disabled. Run `/herald doctor` in the intended guild after login to check actual channel, role and command-registration state.

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

For the supported Linux/Python 3.12 release target, first replace the venv-bundled installer with the hash-pinned version in `pip-bootstrap.txt`, then install the reviewed exact Herald application lock `RottenLabz_Herald_v1.0_Linux_Py312.lock.txt` with hash enforcement. `requirements.txt` is the direct-range manifest used for development and separately qualified platforms; it is not a substitute for either reviewed release file. Never use the system Python package environment as the application environment.

**Linux release/deployment console:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r pip-bootstrap.txt
.venv/bin/python -m pip install --require-hashes -r RottenLabz_Herald_v1.0_Linux_Py312.lock.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
```

For each release, qualify the hash-pinned pip bootstrap and the exact Herald application lock together in a clean Python 3.12 Linux environment. Run the release gates in [RELEASE_CHECKLIST.md](../RELEASE_CHECKLIST.md), including current vulnerability audits of the installed runtime, exact lock and public requirements plus SBOM generation. Review/create `.venv` only in the designated test/deployment workspace.

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
