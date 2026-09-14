# Changelog

## v0.2.0 - Unreleased

### Changed

- Renamed the public project to RottenLabz Herald; the everyday bot name is Herald.
- Unified delivery around content revisions, approval binding and durable SQLite claims; uncertain remote outcomes require owner resolution.
- Added schema versioning and migration that preserves v0.1.1 queue/audit history.
- Added a generic RSS/Atom source configuration with per-source automatic/review policy, bounded retrieval and structured health.
- Kept GamerPower with an explicit attribution backlink alongside the claim link.
- Removed bundled legacy third-party providers; trusted operator-configured local extensions can enter the same queue pipeline.
- Added the /herald slash-command namespace, owner review UI, diagnostics and source-derived subscription controls while retaining owner DM commands.
- Tightened owner/guild authorization, boolean parsing and subscription role permissions.
- Added privacy/third-party notices, release gates and dedicated-user hardened deployment examples.
- Replaced the denylist working-directory NOENV backup with a reviewed committed-source exporter and exact archive verifier.
- Set GitHub as the intended development/release authority and Codeberg as source mirror only. Remote migration remains an operator task.

This entry records the foundation candidate. It is not a tag, release or deployment claim. The complete dependency resolver/integration/release gates remain mandatory.

## v0.1.1 - 2026-07-14

### Fixed

- Fixed Twitch alerts only posting the first broadcast from a watched channel. Twitch stream IDs now identify broadcasts; permanent channel URLs are no longer treated as unique alert identities.
- Existing databases are upgraded by replacing the old globally unique URL index with a normal lookup index; historical Twitch rows and stream IDs are preserved.
- Twitch now obtains a fresh app access token and retries once after a 401 response.
- Twitch channel lists are split into API-safe batches of 100.
- Provider-level failures are now visible in runtime output and service logs instead of being hidden behind an otherwise successful watcher cycle.
- Failed deliveries now requeue automatically on later watcher cycles until the configured attempt cap is reached.
- Pending delivery is now FIFO so old queued items cannot be starved by newer discoveries.
- Concurrent audit writes are serialised so legitimate watcher/owner activity cannot fork the local hash chain.
- SQLite connections are closed deterministically rather than relying on garbage collection.
- Persistent subscription views are registered only once across Discord reconnects.
- Twitch role mentions are resolved inside the destination guild instead of across every connected guild.
- Multi-server connections now fail closed unless HERALD_GUILD_ID selects the intended server.
- The NOENV helper now excludes every private .env variant while retaining the safe .env.example template.

### Changed

- Added provider toggles for free games, GPU updates, Twitch, and security alerts.
- Fresh gaming-focused installs can keep the non-gaming security module disabled. Existing v0.1.0 installs retain their previous security behaviour until SECURITY_ENABLED is set explicitly.
- Disabled modules are omitted from newly posted subscription panels; callbacks remain registered so older panels fail gracefully instead of showing an interaction error.
- Added bounded dependency ranges and focused Twitch/storage regression tests.
- Clarified hardened systemd installation paths and writable-directory requirements.

### Planned separately

- Slash commands and command-handler refactoring are intentionally planned as a separate command UX release so the Twitch bugfix remains small and reviewable.
