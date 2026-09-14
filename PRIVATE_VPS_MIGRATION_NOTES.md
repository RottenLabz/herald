# Private VPS migration notes — RottenLabz Herald v0.2.0

This is a plan for a later authorized migration. This build did **not** inspect, patch, stop or deploy the live private VPS. The historical private path `/home/rotten/herald-angel` and old service name `herald-angel.service` come from the July records; present state may differ. Do not assume the public v0.1.1 snapshot matches every private source/configuration change.

The intended destination is one public core plus protected operator `.env`, generic feed configuration, optional trusted local providers, and private runtime data. It does not require a permanent private fork or a second private PC source repository.

## Before touching live state

1. Inspect the current service unit, actual source/version, Python/dependency inventory, database path and sidecars, runtime feed/API configuration and private source differences. Check permissions without printing secrets. Obtain approval before creating deployment/workspace directories.
2. Stop the old service for the migration window. Ensure only one process uses the operational queue. The new process lease uses the canonical database path plus `.instance.lock`; never delete this lock file while Herald runs. Do not launch old and new workers against the same database; restart recovery deliberately marks unresolved claims uncertain.
3. Save a consistent SQLite backup using SQLite's backup API (or a stopped service and a verified consistent file set), then run `PRAGMA integrity_check`. Record the schema, counts by state/category, audit event count and full audit verification result. Retain the original database and any required sidecars intact until the backup is verified.
4. Separately save private `.env`, custom provider/code changes, feed configuration, service unit, IDs/emoji configuration and required logs with restrictive access. These private rollback artifacts contain sensitive material and must never be shared as source exports.
5. Save reviewed clean committed public source with its checksum. The new NOENV exporter intentionally omits untracked/uncommitted private changes; inventory and privately preserve those separately. Record current source/dependency versions and the exact rollback pairing.
6. Test the v0.2.0 migration on a **copy** of this specific private database in the operator-approved test location. Verify migration is idempotent, counts/history stay readable, posted records are not rewritten, and audit verification passes. Qualify the exact dependencies in a clean supported environment before using them on the VPS.

## Removed public providers

The public upgrade removes implementations/defaults for Guru3D, Twitch Helix, BleepingComputer, CISA, UK NCSC and bundled security-specific feeds. Historical `gpu_updates`, `stream_alerts`, `security_alerts` and other categories remain in the database and readable. Removing a provider implementation neither deletes its old data nor establishes continuing rights to retain it.

For previously approved GPU/security feeds, create generic source entries with stable IDs, private URLs, destination channel IDs and optional harmless role IDs. Preserve an appropriate category label such as `gpu_updates` or `security_alerts` if desired. Choose `review` explicitly for material requiring review; delivery policy belongs to the source rather than its category name. Check source storage/redistribution/attribution terms before enabling it; the public template supplies no replacement third-party URLs. Historic posted rows retain their original provenance. Legacy pending rows become held; they have no validated current source-policy fingerprint and cannot bypass validation through manual approval. Rediscover under the new source configuration, review the resulting current-source rows, and deliberately skip superseded legacy held rows as appropriate. Provider/source identity changes can create separate new rows; inspect this backlog before enabling delivery.

An existing private Twitch implementation can later be adapted into a trusted standalone local module exposing `fetch_items()` and returning the agreed item dictionaries. Configure its existing absolute local directory/module plus operator-controlled source metadata through `HERALD_PRIVATE_PROVIDERS`. Preserve broadcast-specific external IDs for deduplication, and route all discovered items through the public validation/revision/queue coordinator. It must not send directly to Discord. Review applicable API rights, attribution, retention and deletion before migrating; moving code to a private module does not cure data-use obligations. The public release contains no Twitch implementation and this plan creates no real private provider directory.

## Configuration migration

| v0.1.1 setting/behavior | v0.2.0 destination/action |
|---|---|
| `HERALD_NAME=Herald Angel` | Use everyday name `Herald`; public brand constant is RottenLabz Herald. |
| `HERALD_OWNER_ONLY` | Deprecated, may be removed from private `.env`; never grants public mutation access even if false. Fix invalid/blank boolean values. |
| `HERALD_SERVER_COMMANDS_ENABLED` | Keep false unless deliberately retaining owner-only legacy target-guild text commands. |
| New command scope/build identity | Set `HERALD_COMMAND_SCOPE=guild`; optionally supply reviewed `HERALD_BUILD_ID`. |
| `GAMERPOWER_API_URL`, `GAMERPOWER_RSS_URL` | Removed overrides; the built-in provider uses fixed official endpoints. |
| `FREE_GAMES_CHANNEL_NAME` | Configure `FREE_GAMES_CHANNEL_ID`; optional `FREE_GAMES_ROLE_ID` and `FREE_GAMES_DELIVERY_MODE`. |
| Welcome/subscription channel names | Prefer `WELCOME_CHANNEL_ID` / `SUBSCRIPTIONS_CHANNEL_ID`; existing names remain migration fallbacks where supported/unambiguous. |
| `GPU_UPDATES_ENABLED`, `GURU3D_RSS_URL`, GPU channel name | Removed provider keys; use an independently approved generic source, ID-based destination and chosen delivery mode. |
| `SECURITY_ENABLED`, `SECURITY_RSS_URLS`, `HERALD_SECURITY_STRICT_FILTER`, security channel name | Removed bundled logic; configure individually reviewed generic sources. Legacy strict keyword filtering is not automatically recreated. |
| `TWITCH_*` and stream channel name | Removed public integration keys; assess privately for the trusted extension, never copy them into public examples. |
| `HERALD_DB_PATH` | Preserve exact private data for migration; hardened service uses absolute `/var/lib/rottenlabz-herald/herald.db`. |
| New feed/plugin configuration | `HERALD_FEED_CONFIG_PATH` points to private writable JSON; `HERALD_PRIVATE_PROVIDERS=[]` until explicitly adapted/reviewed. |
| `HERALD_STARTUP_BACKLOG_MODE` | Recommended `held`; verify first-start discovery and existing pending/held migration before any automatic posts. |
| GPU/security emoji or historic category labels | May remain for rendering history; they do not enable old integrations. |

## Deployment layout

The proposed hardened layout uses root/deployer-owned source and venv in `/opt/rottenlabz-herald`, root-owned `0600` environment in `/etc/rottenlabz-herald`, and only required writable state under `/var/lib/rottenlabz-herald`, owned by dedicated user `herald`. The example `ProtectHome=true` does not run the old `/home` layout unchanged. Review/install the new service and paths deliberately; do not simply replace the old unit and restart it blindly.

Trusted private source may live in an already approved read-only local path accessible to the service. Never store writable plugin code beside a feed-download output and assume it is safe because it is “local.” Preserve private credentials outside application source.

## After the copied-database test and approved deployment

- Run `/herald doctor`, status/about, audit verify, and feed list; confirm version/build, target guild, actual schema, permissions, role hierarchy, destinations and source health.
- Compare historical counts and sample posted rows/audit records to the baseline. Confirm old categories remain readable and old provider defaults are absent.
- Test owner vs non-owner and wrong-guild commands, an old subscription panel, new source-derived subscriptions, welcome behavior and credential-free diagnostics.
- Keep delivery paused while reviewing pending/held legacy backlog. Test one controlled preview and one announcement in a dedicated authorized channel, including both GamerPower and claim links.
- Prove generic review changes withdraw approval; test discovery failure while healthy pending delivery can continue.
- Restart during a controlled test send and confirm unresolved sends become `uncertain`, never auto-repost. Resolve them deliberately after checking Discord.
- Verify service ownership/modes and write boundaries, inspect sanitized logs, and complete a consistent post-migration backup with integrity/audit checks.

Rollback means stop v0.2.0 and restore the matching old application, dependencies/config and pre-migration database together. Never point v0.1.1 at a database upgraded by v0.2.0 and assume downgrading source alone is supported. Keep rollback artifacts private; do not delete the old installation until the operator accepts the verified result.
