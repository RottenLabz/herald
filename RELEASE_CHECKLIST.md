# RottenLabz Herald v0.2.0 release checklist

This checklist is a gate, not evidence that a release has happened. The four-patch foundation candidate must be qualified on the actual intended release environment. No tag, push, hosted release or live deployment is authorized by this document alone.

## Authority and source review

- [ ] Verify the exact source baseline and apply the four ordered patches with their checksums.
- [ ] Review all changes, private/public boundaries and new explicit source-manifest entries; keep untracked/private changes out of the public candidate.
- [ ] Verify repository-local Git author/committer identity and use the intended noreply address. Inspect annotated-tag identity before publication. Do not invent a name/address or rely on account UI privacy settings to rewrite Git metadata.
- [ ] Verify final committed `HEAD`, matching intended tracking state and clean index/working tree. GitHub is authoritative for development, issues, PRs, tags and releases.

**Release checkout console:**

```bash
git status --short
git var GIT_AUTHOR_IDENT
git var GIT_COMMITTER_IDENT
git show -s --format='Author: %an <%ae>%nCommitter: %cn <%ce>' HEAD
git remote -v
```

Review remote output privately; credentials must not be embedded in remote URLs.

## Supported environment and exact dependencies

- [ ] Use a clean, operator-approved Linux test environment with supported Python 3.12 or newer and record OS/architecture/Python version. Qualify each distributed Python/platform combination; marker-dependent closures can differ.
- [ ] Independently inspect current upstream metadata/advisories for all required minimums. September audit floors are aiohttp 3.14.3, urllib3 2.7.0, idna 3.15 and python-dotenv 1.2.2. This build could not retrieve current metadata or resolve required packages; compatibility remains **BLOCKED pending this gate**.
- [ ] Resolve direct ranges plus `constraints-security.txt` without weakening constraints. Produce a hash-locked exact `requirements-release.lock` from a clean resolver using current pip-tools (or equivalent), then install that lock in a separate clean runtime environment with `--require-hashes`. A frozen list from a dirty/general-purpose environment is not release evidence.
- [ ] Archive exact `pip inspect` and `pip freeze --all` output from the runtime environment. Record tool versions separately; do not accidentally include build/audit tooling in the runtime inventory.
- [ ] Run `python -m pip check` and the complete application tests against that exact closure.
- [ ] Run current `pip-audit` against the exact release lock/environment, retaining its machine-readable report. Investigate every finding; unresolved or unexplained errors do not count as PASS.
- [ ] Generate a CycloneDX or SPDX SBOM from the **actual runtime environment**. Validate it, compare it to the installed inventory and preserve its checksum.
- [ ] Review licences/NOTICE files for every actual redistributed artifact. If dependencies are bundled, include required copyright/licence/NOTICE and MPL source-availability material. Verify no unplanned extras/binaries/assets or unknown licence entries are present.

Illustrative workflow after the two existing designated environments have been selected: set `RELEASE_TOOLS_PYTHON` to the clean tooling interpreter and `RELEASE_RUNTIME_PYTHON` to the clean runtime interpreter. The tools environment needs current pip-tools, pip-audit and cyclonedx-bom. Inspect their installed `--help` output before using version-dependent options; do not install them into the runtime environment just to generate evidence.

**Linux release console — repository root, existing tooling/runtime environments:**

```bash
"$RELEASE_TOOLS_PYTHON" -m piptools compile --generate-hashes --output-file requirements-release.lock requirements.txt
"$RELEASE_RUNTIME_PYTHON" -m pip install --require-hashes -r requirements-release.lock
"$RELEASE_RUNTIME_PYTHON" -m pip check
"$RELEASE_RUNTIME_PYTHON" -m pip inspect > release-inventory.json
"$RELEASE_RUNTIME_PYTHON" -m pip freeze --all
"$RELEASE_TOOLS_PYTHON" -m pip_audit -r requirements-release.lock -f json -o release-audit.json
"$RELEASE_TOOLS_PYTHON" -m cyclonedx_py environment "$RELEASE_RUNTIME_PYTHON" --output-file release-sbom.json
```

These output files are private release evidence until inspected. Publish a reviewed exact lock/SBOM alongside the release only after successful qualification. The candidate intentionally supplies no fabricated lock or claim that these commands succeeded here.

## Application verification

- [ ] Compile/import the source using the real declared runtime dependencies and run the full unit/integration regression suite. Preserve exact command/output and counts; dependency stubs alone cannot qualify the release.
- [ ] Migrate a realistic v0.1.1 database **copy**, check SQLite integrity and audit chain, rerun migration idempotently and compare historical counts/content. Include rollback pairing.
- [ ] Exercise revision approvals, provider/category identity, exclusive claims, queued cancellation, malformed boundary dates and bounded retry/uncertainty recovery.
- [ ] Exercise bounded oversized/decompressed/slow providers, partial health, parser failure, safe Markdown, final URL validation and independent delivery while discovery stalls.
- [ ] Using supported real discord.py and a dedicated test guild, register `/herald`, verify typed command options/ephemeral responses, owner/non-owner/wrong-guild behavior, persistent component dispatch across restart, feed admin/preview, subscriptions and dangerous-role rejection.
- [ ] Verify the owner DM recovery path and disabled-default legacy server text commands. Exercise simultaneous DM/slash/review delivery against the same item.
- [ ] Confirm every GamerPower-derived posted alert has both valid giveaway and active attribution links. Test generic examples without adding restricted provider defaults.
- [ ] Run `/herald doctor` and about/status; confirm the expected commit/build and schema with no secrets/private URLs in shareable diagnostics.

**Release runtime console:**

```bash
"$RELEASE_RUNTIME_PYTHON" -m compileall -q -x '/(?:\.venv|venv|data|logs|backups)/' .
"$RELEASE_RUNTIME_PYTHON" -m unittest discover -s tests -v
```

Live tests require separately authorized test credentials, guild and destinations; no real Discord actions were run during this patch build.

## Security, privacy and release artifacts

- [ ] Verify safe `.env` creation before secret insertion, dedicated user, root/deployer-owned source/venv, `UMask=0077`, explicit runtime paths and systemd hardening in a test service installation.
- [ ] Run the reviewed-source scanner/exporter and adversarial export regression tests. Independently review tracked content and artifact contents for secrets, private URLs/IDs, unintended IP and runtime files.
- [ ] Review `PRIVACY.md` against actual collection, indefinite-retention limitations, operator contact and erasure/backup process. Review all supported source terms and attribution.
- [ ] Review `THIRD_PARTY_NOTICES.md` against the exact artifact. Do not represent removed integrations as supported or the public brand as legally cleared by this build.
- [ ] After final source commit/push and clean-state verification, refresh the checksum-backed combined source snapshot and verify its checksum. Keep source, patch, lock/SBOM and release artifact identities traceable. Do not put private/runtime material in the snapshot.
- [ ] Generate the verified reviewed-source archive plus SHA-256 sidecar using `source_export.py`; confirm the archive contains `.env.example` and no private environment/runtime files. See [docs/SECURITY.md](docs/SECURITY.md) for exact export semantics.

## Authorized publication only after the gates pass

- [ ] Review/create the intended annotated v0.2.0 tag and inspect author/tagger metadata. Never retag an existing public release silently.
- [ ] Publish reviewed source, tag/release notes and approved artifacts through **GitHub**, the sole release authority, when explicitly authorized.
- [ ] Perform an **explicit Codeberg source-mirror push** of the selected branch/tag refs to the operator-verified mirror remote; do not use a blanket mirror push that can remove unrelated refs.
- [ ] Verify matching selected ref identities at both forges. Disable Codeberg issues, PRs, wiki/projects and other duplicate collaboration surfaces; link to authoritative GitHub without creating a competing Codeberg release process.
- [ ] The later GitHub organisation transfer to RottenLabz is a separate operator migration after the release is stable; verify real URLs then rather than guessing them in this patch.

Any unavailable resolver, dependency advisory service, SBOM tool, connected Discord test or real deployment check stays explicitly unchecked. Unit-test success does not replace these gates.
