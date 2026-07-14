# Herald Angel Security Notes

Herald Angel is a self-hosted Discord bot. The server owner/operator is responsible for protecting tokens, configuration, logs, and runtime data.

## Never commit secrets

Do not commit:

- .env
- Discord bot tokens
- Twitch client secrets
- SQLite databases
- logs
- virtual environments
- generated backup archives
- private keys

The public .env.example should contain placeholders only.

## Discord token safety

If your Discord token is ever exposed:

1. Regenerate it in the Discord Developer Portal.
2. Update your private .env.
3. Restart Herald Angel.
4. Check Git history and backups for leaks.

## Command safety

Recommended defaults:

    HERALD_DM_COMMANDS_ENABLED=true
    HERALD_OWNER_ONLY=true
    HERALD_REPLY_TO_NON_OWNER_DMS=false
    HERALD_SERVER_COMMANDS_ENABLED=false

This keeps Herald controlled through owner DMs and avoids normal server-channel command use.

## Server targeting safety

For a normal single-server install:

    HERALD_GUILD_ID=0

If the bot is connected to more than one Discord server, set
`HERALD_GUILD_ID` to the intended server. Herald then scopes alert channels,
subscription panels, role pings, and welcome tests to that server. Without an
explicit target in a multi-server connection, delivery fails closed.

## Mention safety

Herald sanitises provider-controlled text before posting.

Non-stream categories use no allowed mentions.

Twitch stream alerts may ping only the configured alert role when enabled.

Avoid enabling broad mention behaviour.

## Subscription role safety

Subscription buttons can add/remove roles.

Only use harmless notification/viewer roles such as:

    Free Games
    GPU Updates
    Stream Alerts
    Security Alerts

Do not use:

- Admin roles
- Mod roles
- Staff roles
- Privileged roles
- Roles with dangerous channel or server permissions

Keep Herald Angel's bot role below staff/admin roles.

## Provider/feed safety

External feeds can contain unexpected text, URLs, images, or malformed data.

Herald uses provider isolation so one provider failure should not stop the whole discovery cycle.

Provider-controlled display text is sanitised before Discord posting, but server operators should still review their feed sources.

## Audit trail wording

Herald includes a local append-only-style audit trail with hash-chain verification.

This is useful for detecting accidental or basic tampering within the local database history.

It is not tamper-proof.

A server administrator with filesystem/database access can delete, replace, or edit the database.

Do not describe the audit system as legally reliable, immutable, or tamper-proof.

## Backups

Use the included NOENV helper:

    ./backup-noenv.sh

The generated archive should exclude:

- .env
- .git
- .venv
- runtime databases
- logs
- caches
- previous backups

Do not commit generated backup archives.

## Reporting security issues

For now, report security issues through the repository issue tracker or the maintainer's preferred contact method.

Do not post live tokens, secrets, or private server data in public issues.
