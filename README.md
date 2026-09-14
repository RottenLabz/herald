# RottenLabz Herald

RottenLabz Herald is a self-hosted Discord announcement bot. The bot's everyday name is **Herald**. This tree prepares the **v0.2.0 foundation upgrade**; it does not declare a published release.

Herald discovers announcements, records them in SQLite, and sends them through a shared delivery queue. Each source chooses `automatic` delivery or owner `review`. Material revisions invalidate a review approval; durable claims prevent competing commands from sending the same item concurrently. A send with an unknown outcome becomes `uncertain` and needs deliberate owner resolution. Delivery is not guaranteed exactly once.

## Included features

- GamerPower free-game alerts, with a separate clickable attribution backlink and giveaway link.
- Configurable RSS/Atom sources with channel IDs, optional harmless subscription-role IDs, tags, attribution and per-source policy.
- Optional trusted local Python provider extensions, disabled until the operator configures them.
- `/herald` slash commands, ephemeral owner review controls, and owner DM recovery commands.
- Source-derived subscription menus, welcome messages, runtime diagnostics and a local hash-chain audit trail.
- Bounded provider retrieval and independent discovery/delivery scheduling.

GamerPower is the only bundled third-party content integration. Generic feed examples use placeholder URLs. Check each chosen source's terms before fetching, storing or republishing its material. Herald's MIT licence does not license third-party content.

## Start here

Use Python **3.12 or newer**, Git, and a dedicated Discord application. Follow [installation](docs/INSTALL.md), then [configuration](docs/CONFIGURATION.md). The installation guide creates the private `.env` with mode `0600` **before** credentials are inserted.

The v0.2.0 dependency security floors come from the September audit. This candidate still requires a clean supported Linux/Python resolver run, real discord.py integration validation, an exact lock, `pip check`, `pip-audit` and an SBOM before release. See [release checklist](RELEASE_CHECKLIST.md).

Owner commands to begin with:

```text
/herald status
/herald doctor
/herald help
/herald queue review
```

Owner DM fallback:

```text
herald help
herald status
herald audit verify
```

Privileged commands always require `OWNER_ID`. Legacy server text commands are disabled by default. All server operations are scoped to the configured target guild.

## Operations and source exports

The hardened service layout separates root-owned application code under `/opt/rottenlabz-herald` from writable runtime state under `/var/lib/rottenlabz-herald`. A root-owned environment file under `/etc/rottenlabz-herald` supplies credentials. This is a proposed installation layout, not a claim about an existing deployment.

`backup-noenv.sh` now exports **reviewed committed source only**. It requires a clean tracked tree and the explicit `approved-source-manifest.txt`, runs the built-in secret checks, then checks every archive member against committed bytes. It excludes runtime files and does not include untracked or uncommitted private changes. It is not a database or complete private deployment backup. See [security and export notes](docs/SECURITY.md).

Existing Herald Angel v0.1.1 installations must follow [private migration notes](PRIVATE_VPS_MIGRATION_NOTES.md). Historical queue categories and audit rows are preserved; removed provider integrations are not automatically recreated.

## Project authority and licences

**GitHub is authoritative** for development, issues, pull requests, tags and releases. **Codeberg is a source mirror only.** Operators manage remotes and disable duplicate collaboration surfaces separately. Final repository URLs and the later RottenLabz organisation transfer are operator decisions; none are assumed here.

Original project source uses the [MIT licence](LICENSE), copyright 2026 RottenLabz. See [privacy](PRIVACY.md), [third-party notices](THIRD_PARTY_NOTICES.md), and [release history](CHANGELOG.md).

RottenLabz Herald is independently developed and is not affiliated with, sponsored by, or endorsed by Discord or GamerPower. Their names describe interoperability only.
