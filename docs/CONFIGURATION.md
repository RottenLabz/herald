# RottenLabz Herald configuration

Systemd supplies environment values from `/etc/rottenlabz-herald/.env` in the hardened installation. For development, `config.py` reads the project `.env`; `HERALD_ENV_PATH` can select another environment file. Existing process environment values take precedence. Protect the real environment file before adding credentials; see [INSTALL.md](INSTALL.md).

## Core settings

| Setting | Meaning / default |
|---|---|
| `DISCORD_TOKEN` | Private bot token; required. Never publish it. |
| `OWNER_ID` | Discord user ID authorized for privileged operations; required nonzero in a real deployment. |
| `HERALD_GUILD_ID` | Required nonzero target guild ID. Startup rejects 0 even when Herald belongs to only one server. |
| `HERALD_NAME` | Everyday bot name, `Herald`. Project brand/version are separate constants. |
| `HERALD_COMMAND_PREFIX` | Canonical DM prefix, `herald`. |
| `HERALD_COMMAND_SCOPE` | `guild` (default) or `global`; global registration does not relax guild/owner authorization. |
| `HERALD_BUILD_ID` | Optional operator-supplied commit/build label, displayed without executing Git. |
| `HERALD_DM_COMMANDS_ENABLED` | `true`; owner DM recovery transport. |
| `HERALD_SERVER_COMMANDS_ENABLED` | `false`; legacy owner-only target-guild text transport. |
| `HERALD_REPLY_TO_NON_OWNER_DMS` | `false`; controls replies, never grants command access. |
| `HERALD_OWNER_ONLY` | Deprecated compatibility setting. It cannot disable owner authorization, including when explicitly false. |
| `HERALD_DB_PATH` | SQLite path; development default `./data/herald.db`; service example uses `/var/lib/rottenlabz-herald/herald.db`. |
| `HERALD_FEED_CONFIG_PATH` | Writable JSON source config; development default `./data/feeds.json`; service path `/var/lib/rottenlabz-herald/feeds.json`. |
| `HERALD_PRIVATE_PROVIDERS` | JSON list of trusted local provider descriptors; default `[]` (disabled). |

Boolean values accept explicit true/false spellings; unknown or blank values are configuration errors. Do not retain blank boolean assignments during migration.

## Built-in GamerPower source

```dotenv
FREE_GAMES_ENABLED=true
FREE_GAMES_CHANNEL_ID=0
FREE_GAMES_ROLE_ID=0
FREE_GAMES_DELIVERY_MODE=automatic
HERALD_FREE_GAME_STRICT_FILTER=true
```

The built-in provider uses fixed official GamerPower endpoints; old `GAMERPOWER_API_URL` and `GAMERPOWER_RSS_URL` overrides are no longer read. Set the destination channel ID. Role ID 0 means no subscription/ping role. Each GamerPower-derived alert carries a separate clickable GamerPower backlink as well as its valid giveaway link. An `automatic` source still enters SQLite and the shared delivery coordinator; it never sends directly from a provider.

## Generic RSS/Atom configuration

The versioned JSON file is runtime state, **not public tracked source**. Source IDs are stable identities: changing a source's name need not change its ID. A source has a display name, URL, enabled flag, destination channel ID, `automatic` or `review` policy, optional subscription role ID, category, source/homepage URL, attribution, tags and a privacy marker.

Example only; the feed is disabled and its URL is a placeholder:

```json
{
  "version": 1,
  "feeds": [
    {
      "id": "example-feed",
      "name": "Example Feed",
      "url": "https://example.com/feed.xml",
      "enabled": false,
      "channel_id": 123456789012345678,
      "delivery_mode": "review",
      "role_id": null,
      "category": "announcements",
      "homepage_url": "https://example.com/",
      "attribution_label": "Example",
      "attribution_url": "https://example.com/",
      "tags": [],
      "private": false
    }
  ]
}
```

Use `/herald feed add` with a stable source ID, feed name, URL, destination channel, delivery policy and optional notification role. `/herald feed test` fetches without posting or storing an announcement; `/herald feed preview` renders an owner preview. Enable/disable/remove are explicit owner actions; removal preserves historical queue and audit records. Feed config writes are atomic. Changing a previously reviewed material item revokes its approval.

Only valid HTTP(S) URLs are accepted. Treat all configured endpoints as operator-authorized network destinations. No public third-party feed defaults are supplied beyond GamerPower. RSS/Atom availability does not itself grant storage/redistribution rights: review the chosen source terms and use permitted content, canonical links and required attribution. Mark private sources `private: true`; review diagnostic previews before sharing any deployment information.

## Trusted local extension interface

A descriptor uses the same source metadata as a feed, plus `path` and `module`:

```json
[
  {
    "path": "/operator/existing/trusted-modules",
    "module": "example_provider",
    "source": {
      "id": "private-example",
      "name": "Private Example",
      "url": "https://example.com/",
      "enabled": false,
      "channel_id": 123456789012345678,
      "delivery_mode": "review",
      "role_id": null,
      "category": "announcements",
      "private": true
    }
  }
]
```

This illustrates configuration only; it does not create the directory or provide an implementation. In a `.env` file enclose the compact JSON in single quotes so JSON double quotes remain intact. The configured absolute directory must already exist and contain reviewed local Python modules. The module exports `fetch_items()` returning a bounded list of dictionaries. Source identity, destination and policy come from the operator descriptor and are enforced by Herald before queue insertion.

Plugins execute **trusted Python with the process account's privileges**. Worker termination/time budgets provide operational limits, not a security sandbox. They can access process credentials and allowed files/network. Protect plugin code from writes by the service account and untrusted users. There is no download, marketplace or remote code installation. Providers return items; they must not call Discord or bypass the queue. Private provider authors are responsible for API terms, retention and data minimization.

## Delivery and health

| Setting | Default / effect |
|---|---|
| `HERALD_AUTO_POST_ENABLED` | `true`; automatic delivery master pause/control, without replacing per-source policy. |
| `HERALD_CHECK_SECONDS` | `3600`, bounded to 60–86400 seconds; discovery cadence. |
| `HERALD_DELIVERY_SECONDS` | `15`, bounded to 5–3600 seconds; delivery cadence independent of discovery. |
| `HERALD_STARTUP_BACKLOG_MODE` | `held`; first discovery backlog is held for owner review. `pending`/`skipped` are explicit alternatives. Review-source policy is still enforced. |
| `HERALD_POST_BATCH_LIMIT` | `10`, bounded to 1–100. |
| `HERALD_DELIVERY_MAX_ATTEMPTS` | `5`, bounded to 1–20 for known failed attempts. |

When material content changes on a revisable queue row, the new revision receives a fresh retry budget and clears old delivery errors, receipts and claim metadata. Unchanged rediscovery preserves legitimate retry state; posted, sending and uncertain rows retain their frozen historical contents.

Retrieval is bounded to 1 MiB decoded response, 16 KiB chunks/raw text processing, 50 inspected entries per source, 256-character titles; overlong external IDs are hashed from their complete bounded identity instead of display-truncated, 1500-character summaries, 2048-character URLs, 16 tags of 32 characters, and 100-character dates. Connect/read budgets are 5 seconds, an operation has a 20-second deadline, a source worker is terminated at 25 seconds, and the discovery cycle has a 120-second budget. Configuration permits at most 32 feeds and 16 private descriptors. Redirects are refused: configure the final permitted feed URL. Parser work runs inside the bounded worker. On supported POSIX systems workers also set 512 MiB address-space and 20-second CPU limits. Feed config's parent directory must already exist. On Windows, protect it with restrictive native ACLs; POSIX permission modes do not provide a Windows ACL guarantee.

Provider health distinguishes healthy items, healthy empty, partial/degraded, failed and disabled. `/herald status` summarizes health/queue/runtime state; `/herald doctor` exposes sanitized detailed checks.

The explicit owner `/herald run` / `herald run once` operation performs bounded discovery and attempts pending delivery even when automatic delivery is paused. Use `herald discover` for discovery into held without a send, or feed test/preview for a dry run. Queue post/deliver are also explicit owner actions.

`uncertain` means Discord might already have accepted the send. It never automatically retries. Check the actual destination; then explicitly record the existing Discord message ID, skip it, or deliberately retry while accepting duplicate risk. Neither a review button nor an old queued list overrides the current database revision/claim.

Owner DM syntax is `herald resolve <id> posted <message_id>`, `herald resolve <id> retry` or `herald resolve <id> skip`. Resolution names are lowercase; `posted` requires 1–20 ASCII digits for the Discord message ID, with the same validation as `/herald queue resolve`. Both commands revalidate the current state and revision. Sending the DM `retry` command explicitly acknowledges possible duplicate delivery after checking Discord; slash resolution requires its acknowledgement option. A retry is never automatic recovery from uncertainty.

## Welcome, subscriptions and display

`WELCOME_ENABLED=true` controls member welcomes. Prefer `WELCOME_CHANNEL_ID` and `SUBSCRIPTIONS_CHANNEL_ID`; legacy name settings `WELCOME_CHANNEL_NAME` / `SUBSCRIPTIONS_CHANNEL_NAME` remain for migration where unambiguous. Source subscription choices derive from configured role IDs. Use only harmless notification roles below the bot role. View Channel and Read Message History may be explicitly allowed only on that source's configured alert destination; explicit View Channel allows on unrelated channels, categories or voice channels are rejected. Base View Channel and administrative/moderation permissions are rejected. Panels whose role or destination binding has changed, and panels outside the target guild, must not mutate roles.

Optional Unicode/custom emoji values include `HERALD_EMOJI_HERALD` (default 🎺), `HERALD_EMOJI_WELCOME` (default 👋) and `HERALD_EMOJI_FREE_GAME`. Welcome messages use the distinct `welcome` slot: `HERALD_EMOJI_WELCOME=👋` is independent of the general Herald emoji. Preserve an existing private welcome value during migration. Historical GPU/security emoji keys can remain for rendering old categories, without reinstating removed providers. Keep private custom emoji and server-specific IDs in private configuration.

See [PRIVATE_VPS_MIGRATION_NOTES.md](../PRIVATE_VPS_MIGRATION_NOTES.md) for removed/deprecated v0.1.1 provider keys.
