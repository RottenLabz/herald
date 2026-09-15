# RottenLabz Herald v1.0.0 release checklist

This checklist is a gate, not evidence that a release has happened. No tag, push, hosted release or live deployment is authorized by this document alone.

## Pre-release qualification already obtained

Before the final v1.0.0 identity/branding change, the reviewed foundation completed the following on 14 September 2026:

- Windows 11 / Python 3.12 with real discord.py: 185 tests passed with four expected platform skips; `pip check` passed.
- Clean Linux x86_64 / Python 3.12: 185 tests passed with zero skips.
- Exact hash-locked Linux runtime: installed with `--require-hashes`, `pip check` passed, then 185 tests passed with zero skips.
- `pip-audit` reported no known vulnerabilities in both the exact qualified runtime and a fresh public-requirements resolution at that time.
- Windows and Linux CycloneDX SBOMs plus SHA-256 sidecars were generated.

Those results are evidence for the foundation, not a substitute for rerunning the final gates after this release-identity/asset change. Vulnerability results are time-sensitive.

## Authority and source review

- [ ] Verify final committed `HEAD`, matching intended tracking state and clean index/working tree.
- [ ] Review all final changes, private/public boundaries, the logo/branding notice and every explicit source-manifest entry.
- [ ] Verify repository-local Git author/committer identity and intended noreply address. Inspect annotated-tag identity before publication.
- [ ] Verify GitHub is the authoritative development/release remote and review remote URLs privately for embedded credentials.

**Release checkout console:**

```bash
git status --short
git var GIT_AUTHOR_IDENT
git var GIT_COMMITTER_IDENT
git show -s --format='Author: %an <%ae>%nCommitter: %cn <%ce>' HEAD
git remote -v
```

## Supported environment and exact dependencies

The primary v1.0.0 release runtime is Linux x86_64 / Python 3.12. The reviewed exact lock is `RottenLabz_Herald_v1.0_Linux_Py312.lock.txt`. Windows 11 / Python 3.12 is separately regression-qualified; other Python/platform closures require their own qualification.

- [ ] Reinstall the final Linux candidate from the exact lock using `--require-hashes` in a new clean runtime.
- [ ] Run `python -m pip check` against that exact closure.
- [ ] Run the complete application test suite against that exact closure.
- [ ] Run current `pip-audit` against the exact lock and current public requirements; investigate every finding or tool error.
- [ ] Generate/validate the CycloneDX SBOM from the actual final runtime and preserve its SHA-256.
- [ ] Archive exact final runtime inventory and lock/SBOM identities as release evidence.
- [ ] Review licences/NOTICE obligations for every actual redistributed artifact; do not treat the SBOM as a legal opinion.

**Linux release console — repository root:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r RottenLabz_Herald_v1.0_Linux_Py312.lock.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
```

## Application verification

- [ ] Compile/import the final source using the real declared runtime dependencies and run the full unit/integration regression suite. Preserve exact command/output and counts.
- [ ] Migrate a realistic v0.1.1 database **copy**, check SQLite integrity and audit chain, rerun migration idempotently and compare historical counts/content. Include rollback pairing.
- [ ] Exercise revision approvals, provider/category identity, exclusive claims, queued cancellation, malformed boundary dates and bounded retry/uncertainty recovery.
- [ ] Exercise bounded oversized/decompressed/slow providers, partial health, parser failure, safe Markdown, final URL validation and independent delivery while discovery stalls.
- [ ] Using supported real discord.py and a dedicated test guild, register `/herald`, verify typed command options/ephemeral responses, owner/non-owner/wrong-guild behavior, persistent component dispatch across restart, feed admin/preview, subscriptions and dangerous-role rejection.
- [ ] Verify the owner DM recovery path and disabled-default legacy server text commands. Exercise simultaneous DM/slash/review delivery against the same item.
- [ ] Confirm every GamerPower-derived posted alert has both valid giveaway and active attribution links.
- [ ] Run `/herald doctor` and about/status; confirm v1.0.0, expected commit/build and schema with no secrets/private URLs in shareable diagnostics.

## Security, privacy, branding and release artifacts

- [ ] Verify safe `.env` creation before secret insertion, dedicated user, root/deployer-owned source/venv, `UMask=0077`, explicit runtime paths and systemd hardening in a test service installation.
- [ ] Run the reviewed-source scanner/exporter and adversarial export regression tests. Independently review tracked content and artifact contents for secrets, private URLs/IDs, unintended IP and runtime files.
- [ ] Verify `RottenLabz_Herald_Logo.png` matches the exact reviewed binary identity enforced by `source_export.py`; review [BRANDING.md](BRANDING.md).
- [ ] Review `PRIVACY.md` against actual collection, retention, operator contact and erasure/backup process.
- [ ] Review `THIRD_PARTY_NOTICES.md` against the exact artifact and current supported-source terms/attribution.
- [ ] After the final source commit/push and clean-state verification, refresh the checksum-backed combined source snapshot and verify its checksum. Do not put private/runtime material in the snapshot.
- [ ] Generate the verified reviewed-source archive plus SHA-256 sidecar using `source_export.py`; confirm it contains `.env.example`, the reviewed logo and lock, and no private environment/runtime files.

## Authorized publication only after the gates pass

- [ ] Review/create the intended annotated `v1.0.0` tag and inspect tagger metadata. Never retag an existing public release silently.
- [ ] Publish reviewed source, tag/release notes and approved lock/SBOM/source-export artifacts through **GitHub**, the sole release authority, only when explicitly authorized.
- [ ] Perform an explicit Codeberg source-mirror push of selected branch/tag refs; do not use a blanket mirror push that can remove unrelated refs.
- [ ] Verify matching selected ref identities at both forges. Disable Codeberg issues, PRs, wiki/projects and other duplicate collaboration surfaces; link to authoritative GitHub without creating a competing release process.
- [ ] Treat the later GitHub organisation transfer to RottenLabz as a separate operator migration after the release is stable.

Any unavailable live Discord, deployment, migration or publication check stays explicitly unchecked. Unit-test success does not replace those gates.
