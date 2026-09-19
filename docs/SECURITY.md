# RottenLabz Herald security and source exports

The operator controls the host, Discord credentials, permitted content sources and trusted local provider code. Protect that boundary. The application cannot protect its secrets or audit history from an administrator who already controls its files/process.

## Commands and roles

Privileged operations always require the configured `OWNER_ID`. `HERALD_OWNER_ONLY` is a deprecated compatibility value and cannot disable this check. Invalid/blank booleans fail at configuration loading. Legacy server text commands default off; enabling them retains owner and target-guild checks. Global slash registration does not grant authority in other guilds.

New subscription panels use configured source role IDs and enforce target guild, role existence/uniqueness, Manage Roles, hierarchy and dangerous-permission rejection. Use harmless notification roles only, below the bot role and below staff. Provider display text is escaped, and allowed mentions are restricted to the intended configured role where applicable; provider input must not authorize arbitrary mentions.

## Content and delivery

Approval binds to material content revisions. Every delivery path claims/reloads the current item through the same coordinator. Once a send is in flight, an owner cancellation must report that state honestly. A remotely accepted message followed by a local crash creates an uncertain result: inspect the destination before explicitly resolving it. Automatic retry is reserved for known safe failures; `uncertain` never silently reposts.

Provider responses are untrusted data. Bounds and worker termination isolate malformed/slow retrieval; a separate delivery task can continue delivering queued records. Trusted local plugins remain privileged code, not untrusted sandboxed extensions. Never configure code downloaded automatically from a feed or allow an untrusted account to edit source/plugins.

## Files and tokens

Create `.env` with `0600` before entering credentials. The hardened service uses a root-owned `/etc` environment file, non-writable `/opt` application code/venv and a dedicated user with writable `/var/lib/rottenlabz-herald` state. `UMask=0077` restricts new runtime files but does not fix preexisting permissions. Keep real configuration, private plugins, databases, SQLite sidecars, logs, caches and backups outside tracked public source. Herald holds a process lease at the canonical database path plus `.instance.lock`; never delete that lock file while any instance runs. A second instance must fail closed.

If a Discord token is exposed, regenerate it in the Developer Portal, replace it privately and restart. Assess source history, published artifacts and backups; deleting one working file does not retract old copies. Do not paste secrets into public issues or diagnostic output.

## Reviewed committed-source exporter

`approved-source-manifest.txt` is an explicit reviewed file list, not a filename glob. When public source files are added/removed, review the actual content and update this manifest in the same change. The only reviewed binary exception in the current public source set is `RottenLabz_Herald_Logo.png`; `source_export.py` accepts it only when the filename, PNG signature, byte length and SHA-256 all match the hard-coded reviewed identity. Any logo byte change therefore requires an explicit source-exporter and manifest review as well as the branding review in [BRANDING.md](../BRANDING.md). Never regenerate it blindly from an arbitrary private checkout.

The exporter requires Git and Python 3.12+. It reads regular committed Git blobs from `HEAD`; tracked index/working-tree changes cause failure. Untracked files are omitted. Tracked real environment files, configured runtime/private paths, databases and SQLite sidecars cause failure even if listed in the manifest; they are never silently hidden. Unexpected tracked source also causes failure. The selected archive must contain the exact complete approved manifest, including public `.env.example`. An approved source colliding with an actual configured runtime/private path causes failure. Symlinks, submodules, unsafe paths, binary source and excessive files fail closed.

Runtime `.env` is parsed as data, **never shell-sourced**. Supply `--env-file` if the runtime configuration is outside the checkout and not already supplied through `HERALD_ENV_PATH`/process environment. Standard single-line assignments/quoting are supported; complex/multiline syntax or unresolved runtime path expansion is refused. Set explicit absolute runtime paths for exports of systemd layouts. Do not run `source .env` as an export workaround.

Recognized runtime path settings are explicitly limited to `HERALD_ENV_PATH`, `HERALD_DB_PATH` and `HERALD_FEED_CONFIG_PATH`, alongside the supplied environment file, default runtime directories and `HERALD_PRIVATE_PROVIDERS` paths. Arbitrary `HERALD_*_PATH` / `HERALD_*_DIR` names cannot change the approved source set. If tracked private material causes a refusal, investigate and correct the source/history exposure separately; changing the manifest to omit it cannot make this exporter pass.

The built-in scanner checks credential assignments, private-key markers, common Discord/GitHub/API token patterns, and credential-bearing URLs. Any read/scanner/Git/verification failure stops the export. These checks cannot prove absence of every secret, private identifier or copyrighted content: manual source review remains mandatory. Never silence scanner errors or interpret a missing scanner's exit status as success.

**Linux release console, existing clean reviewed checkout — select an existing output directory:**

```bash
python3 source_export.py --check
python3 source_export.py --output ../rottenlabz-herald-source.tar.gz
python3 source_export.py --verify ../rottenlabz-herald-source.tar.gz
```

The sidecar names the archive basename. From the archive's parent directory, run `sha256sum -c rottenlabz-herald-source.tar.gz.sha256`. The output/sidecar names must not already exist; no output directories are created. The archive is first created privately, verified member-by-member against the scanned committed bytes, and only then published without overwriting an existing path. Failure removes partial output. The compatibility entry point accepts the same options:

```bash
./backup-noenv.sh --output ../rottenlabz-herald-source.tar.gz
```

A source archive excludes **all untracked and uncommitted private modifications**. It cannot substitute for inspecting a private VPS's custom code, trusted modules, configuration and persistent data before migration. Make separate private, consistent database/configuration backups, protect them and verify their restore plan. Never upload those backups as a public source export.

## Audit limitations and reporting

The audit trail is a local append-only-style hash chain. Verification detects inconsistency relative to its own recorded chain; a host administrator can replace/delete it. It is not immutable, legally certified or tamper-proof. See [PRIVACY.md](../PRIVACY.md) for retention/deletion limitations.

Use the authoritative GitHub repository's documented security-contact route once configured. If no private reporting route is published, ask the maintainer for one without posting exploit secrets or live private data. Do not create duplicate reports in the Codeberg mirror.
