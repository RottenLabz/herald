"""Owner diagnostics use fixed messages and allowlisted metadata only.

No raw exception, feed body/URL/name, plugin path or configuration dump belongs
in this module's output. The diagnostic text is suitable for a public issue.
"""

import platform
import re
import time

import discord

import config
import storage
from version import PROJECT_NAME, VERSION

STARTED_AT = time.monotonic()
QUEUE_STATES = ("held", "pending", "sending", "uncertain", "failed", "posted", "skipped")
HEALTH_STATES = ("healthy", "empty", "degraded", "failed", "disabled")


def build_identity():
    value = str(getattr(config, "HERALD_BUILD_ID", ""))
    return value if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", value) else "not supplied"


def target_guild(services):
    guild_id = getattr(config, "HERALD_GUILD_ID", 0)
    if not isinstance(guild_id, int) or guild_id <= 0:
        return None
    resolver = getattr(services, "configured_guild", None)
    guild = resolver() if callable(resolver) else resolver
    return guild if guild is not None and guild.id == guild_id else None


def source_policies():
    from provider_runtime import get_configured_sources
    return get_configured_sources()


def provider_health(services):
    runtime = getattr(services, "WATCHER_RUNTIME", {})
    discovery = runtime.get("last_discovery") or {}
    health = discovery.get("health") or []
    if not health:
        from provider_runtime import discovery_worker
        health = getattr(discovery_worker, "last_health", [])
    return health


def _counts():
    return {state: storage.count_items_by_status(state) for state in QUEUE_STATES}


def _health_text(health):
    counts = {state: 0 for state in HEALTH_STATES}
    for record in health:
        state = record.get("status")
        counts[state if state in counts else "failed"] += 1
    return ", ".join(f"{state} {count}" for state, count in counts.items())


def _safe_time(value):
    # Permit timestamps only; an error string or credential cannot leak here.
    value = str(value or "")
    return value if re.fullmatch(r"[0-9TtZz:+. /-]{10,40}", value) else "never/unknown"


def about_text(services=None):
    try:
        schema = str(int(storage.schema_version()))
    except Exception:
        schema = "unavailable"
    return (
        f"{PROJECT_NAME} {VERSION}\nDB schema: {schema}\n"
        f"Python: {platform.python_version()}\ndiscord.py: {discord.__version__}\n"
        f"Uptime: {max(0, int(time.monotonic() - STARTED_AT))} seconds\n"
        f"Build: {build_identity()}"
    )


def status_text(services):
    runtime = getattr(services, "WATCHER_RUNTIME", {})
    guild = target_guild(services)
    lines = [f"{PROJECT_NAME} {VERSION} · build {build_identity()}",
             f"Target guild: {guild.id if guild else 'unavailable'}",
             f"Watcher: {'running' if runtime.get('running') else 'stopped'}",
             f"Last successful cycle: {_safe_time(runtime.get('last_success'))}"]
    try:
        from provider_runtime import discovery_worker
        lines.append(f"Discovery: {'active' if discovery_worker.running else 'idle'}")
        sources = source_policies()
        enabled = [source for source in sources if source.get("enabled", True)]
        lines.append(f"Sources: {len(enabled)} enabled / {len(sources)} configured; "
                     f"automatic {sum(s.get('delivery_mode') == 'automatic' for s in enabled)}, "
                     f"review {sum(s.get('delivery_mode') == 'review' for s in enabled)}")
        lines.append("Provider health: " + _health_text(provider_health(services)))
    except Exception:
        lines.append("Source configuration/health: unavailable; run /herald doctor")
    try:
        lines.append("Queue: " + ", ".join(f"{s} {n}" for s, n in _counts().items()))
        lines.append(f"Database: accessible, schema {int(storage.schema_version())}")
        # Full chain verification is deliberately reserved for doctor/audit verify.
        lines.append(f"Audit: {int(storage.count_audit_events())} events; /herald audit verify checks integrity")
    except Exception:
        lines.append("Database/audit: unavailable")
    delivery = runtime.get("last_delivery") or {}
    lines.append("Last delivery: " + ", ".join(
        f"{name} {max(0, int(delivery.get(name, 0) or 0))}"
        for name in ("posted", "failed", "uncertain")
        if isinstance(delivery.get(name, 0), (int, bool))))
    lines.append("Recent error: " + ("present; run /herald doctor" if
                 runtime.get("last_error") or runtime.get("last_provider_errors") else "none recorded"))
    return "\n".join(lines)


def build_diagnostics(services, client=None):
    """Return (PASS|WARN|FAIL, fixed check name, sanitised detail) rows."""
    rows = []
    def add(level, check, detail):
        rows.append((level, check, detail))

    guild = target_guild(services)
    add("PASS" if guild else "FAIL", "Target guild", "Configured target available" if guild else "Configured target unavailable")
    client = client or getattr(services, "client", None)
    state = getattr(client, "_herald_registration_state", "not_registered")
    state = state if state in ("registered", "synced", "sync_failed", "not_registered") else "not_registered"
    add("PASS" if state == "synced" else "WARN", "Commands", state)
    try:
        import feed_config
        feed_config.load_config()
        sources = source_policies()
        add("PASS", "Feed schema", f"Validated; {len(sources)} sources configured")
    except Exception:
        sources = []
        add("FAIL", "Feed schema", "Configuration cannot be validated")

    member = getattr(guild, "me", None) if guild else None
    permissions = getattr(member, "guild_permissions", None)
    needed = ("view_channel", "send_messages", "embed_links")
    missing = [name for name in needed if not getattr(permissions, name, False)]
    add("FAIL" if missing else "PASS", "Discord permissions", "Missing: " + ", ".join(missing) if missing else "Basic message permissions available")
    enabled = [source for source in sources if source.get("enabled", True)]
    destinations = {source.get("channel_id") for source in enabled}
    channel_bad = 0
    for channel_id in destinations:
        channel = guild.get_channel(channel_id) if guild and isinstance(channel_id, int) else None
        if not isinstance(channel, discord.TextChannel) or member is None:
            channel_bad += 1
            continue
        resolved = channel.permissions_for(member)
        if any(not getattr(resolved, name, False) for name in needed):
            channel_bad += 1
    add("FAIL" if channel_bad else "PASS", "Destination channels", f"{channel_bad} unavailable or missing permissions / {len(destinations)} configured")
    try:
        from subscriptions import subscription_diagnostics
        checks = subscription_diagnostics(guild)
        levels = [check.get("status") for check in checks]
        level = "FAIL" if "FAIL" in levels else "WARN" if "WARN" in levels else "PASS"
        # Keep collaborator diagnostics behind our fixed-string output boundary.
        add(level, "Subscription roles", "Role configuration failed validation" if level == "FAIL" else
            "No subscription roles configured" if level == "WARN" else "IDs, uniqueness, hierarchy and permissions validated")
    except Exception:
        add("FAIL", "Subscription roles", "Role safety validation unavailable")
    try:
        counts = _counts()
        add("PASS", "Database", f"Accessible; schema {int(storage.schema_version())}")
        audit = storage.verify_audit_chain()
        add("PASS" if audit.get("ok") else "FAIL", "Audit integrity", "Verified" if audit.get("ok") else "Integrity verification failed")
        add("WARN" if any(counts[s] for s in ("sending", "uncertain", "failed")) else "PASS",
            "Queue recovery", ", ".join(f"{s} {counts[s]}" for s in ("sending", "uncertain", "failed")))
    except Exception:
        add("FAIL", "Database/audit", "Access or schema validation failed")
    try:
        health = provider_health(services)
        degraded = any(row.get("status") in ("failed", "degraded") for row in health)
        add("WARN" if degraded or not health else "PASS", "Provider health", _health_text(health) if health else "No completed discovery yet")
    except Exception:
        add("WARN", "Provider health", "Health unavailable")
    runtime = getattr(services, "WATCHER_RUNTIME", {})
    discovery_error = bool(runtime.get("last_error") or runtime.get("last_provider_errors"))
    delivery = runtime.get("last_delivery") or {}
    delivery_error = bool(runtime.get("last_delivery_error") or delivery.get("failed") or delivery.get("uncertain") or delivery.get("error"))
    add("WARN" if discovery_error else "PASS", "Last discovery error", "Recorded; raw details withheld" if discovery_error else "None recorded")
    add("WARN" if delivery_error else "PASS", "Last delivery error", "Recorded; raw details withheld" if delivery_error else "None recorded")
    add("PASS", "Herald", VERSION)
    add("PASS", "Python", platform.python_version())
    add("PASS", "discord.py", discord.__version__)
    add("PASS" if build_identity() != "not supplied" else "WARN", "Build", build_identity())
    return rows


def doctor_text(services, client=None):
    return "\n".join(f"{level} · {name}: {detail}" for level, name, detail in build_diagnostics(services, client))
