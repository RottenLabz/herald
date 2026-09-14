# RottenLabz Herald privacy and data handling

This describes the public v0.2.0 self-hosted application. It is a starting notice for operators to adapt, not an assertion about every private installation or a compliance guarantee.

## Who operates an instance

Each server operator runs their own Herald process, supplies their own Discord application credentials, and controls its configuration, machine, database and backups. The public project does not operate a shared hosted backend for these instances. Operators must publish an accessible instance-specific notice, identify themselves/contact details, and explain their chosen sources, retention and request-handling process before users rely on the service.

The project does not sell Discord, provider API or user data. The public core does not send the local database to a project-operated analytics service. Private changes/extensions can alter behavior and must be disclosed by their operator.

## Data processed and stored

Herald receives Discord account/member, guild, channel, message and role identifiers as needed to authorize owner commands, select destinations, welcome members, assign/remove harmless subscription roles and post announcements. It processes the owner command/interaction content needed to perform a requested action. Roles and published messages also remain in Discord's systems.

The local event table can record event type, guild/channel/user IDs, timestamps and event details. Queue records store source identity, provider external IDs, category, title, canonical URLs, summary, tags, presentation dates and image/attribution references, together with state, revision/digest, approvals, delivery attempts/claims, errors and posted Discord message IDs. Audit records include actions, actors, timestamps, item references, state changes, payloads and hash-chain fields. Historical v0.1.1 rows may retain data from removed integrations.

Provider/feed configuration contains URLs, destinations, role IDs and source policy. Some URLs may themselves contain private information. Credentials belong in protected runtime configuration, and operators must also treat custom feed/plugin configuration as private. Status/doctor output is intended to be sanitized; review logs and any artifact before sharing it publicly. A privately marked source hides its URL from shareable diagnostics; this is not encryption of local data.

## Purpose and external services

The data supports source discovery, owner review, deduplication, reliable delivery, failure diagnosis, subscription handling and a local operational audit trail. GamerPower supplies the included free-game source. Generic RSS/Atom endpoints are selected by the operator. Discord receives bot communications and stores posted content/role state under its own policies. Outbound service providers can observe the host's network address and requests; links/images shown in Discord can result in additional requests by Discord or its clients.

Trusted private providers execute local Python with the process account's privileges. They can contact additional services and process additional data. The operator must assess their behavior, API terms, content rights, data minimization, deletion and retention requirements. The public core cannot guarantee what trusted extensions do.

See [Discord privacy policy](https://discord.com/privacy) and the notices/terms for each enabled source. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) identifies the supported public services and software families.

## Local storage, retention and deletion

The configured SQLite file, its possible journal/WAL/SHM sidecars, runtime feed JSON and operational logs are stored on the operator's machine. The recommended service keeps writable state under `/var/lib/rottenlabz-herald` and credentials under `/etc/rottenlabz-herald`. Files are not encrypted by the application; filesystem controls, disk encryption and host security belong to the operator.

The public core has **no automatic retention expiry or user-erasure command** for its queue/event/audit history. Records remain until the operator deliberately removes or replaces them. Disabling/removing a source stops future use as configured but does not erase its historical records or previously published Discord messages. A local hash chain is not immutable or tamper-proof and does not justify indefinite retention.

Operators must choose a documented, appropriate retention period for their data and source terms. For an erasure request, stop processing as needed, locate the relevant local and Discord records, and assess an intentional maintenance/deletion workflow. Do not edit audit rows and then claim the original chain still verifies. If retaining an operational baseline is necessary, document the deletion and start a reviewed new database/audit baseline after minimizing retained data. This release supplies no automated selective audit-chain rewrite. Obtain the relevant operator's assistance through their published contact; the public maintainer cannot delete another operator's database or Discord messages.

## Backups and disclosure

Private database/configuration backups can retain the same information after live deletion. Operators must secure them, set expiry, and apply the documented deletion/restore policy. A restore can reintroduce previously deleted records; account for this explicitly. Do not publish a database, logs or private configuration in a bug report.

The reviewed committed-source exporter omits private/runtime files and is not a backup of the running instance. Its secret checks are bounded heuristics, not proof that all private information has been removed. Operators must review public source and artifacts before sharing them.
