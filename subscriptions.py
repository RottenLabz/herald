"""Configured, role-ID-only subscriptions; legacy components never mutate roles."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import re
from typing import Any

import discord


LEGACY_SUBSCRIPTIONS = {
    "free-games": "Free Games",
    "gpu-updates": "GPU Updates",
    "stream-alerts": "Stream Alerts",
    "security-alerts": "Security Alerts",
}
# Permissions that could turn an opt-in notification role into a privileged role.
# Check channel overwrite grants too: base role permissions alone are insufficient.
DANGEROUS_PERMISSIONS = frozenset({
    "administrator", "kick_members", "ban_members", "manage_channels",
    "manage_guild", "manage_messages", "manage_roles", "manage_permissions",
    "manage_webhooks", "manage_nicknames", "mute_members", "deafen_members",
    "move_members", "moderate_members", "manage_threads", "mention_everyone",
    "manage_emojis", "manage_emojis_and_stickers", "manage_expressions",
    "manage_events", "view_audit_log", "view_guild_insights",
    "view_creator_monetization_analytics", "bypass_slowmode", "pin_messages",
})
STAFF_ROLE_NAME = re.compile(r"(?:^|[^a-z0-9])(?:admins?|administrators?|mods?|moderators?|staff)(?:$|[^a-z0-9])", re.I)
PAGE_SIZE = 25
MAX_SOURCES = 256
_target_guild_id = 0
_source_supplier: Callable[[], Iterable[Any]] = lambda: ()


class SubscriptionConfigurationError(ValueError):
    """Only fixed, non-secret descriptions are exposed by this module."""


@dataclass(frozen=True)
class SubscriptionDefinition:
    source_id: str
    name: str
    channel_id: int
    role_id: int

    @property
    def token(self) -> str:
        # A stale panel cannot grant a different role after a configuration edit.
        return hashlib.sha256(f"{self.source_id}\0{self.role_id}".encode()).hexdigest()[:32]


def configure_subscriptions(
    target_guild_id: int,
    sources: Iterable[Any] | Callable[[], Iterable[Any]],
) -> None:
    """Supply current source descriptors (a callable permits atomic config reloads)."""
    global _target_guild_id, _source_supplier
    _target_guild_id = target_guild_id if type(target_guild_id) is int and target_guild_id > 0 else 0
    if callable(sources):
        _source_supplier = sources
    else:
        fixed = tuple(sources)
        _source_supplier = lambda: fixed


def _value(source: Any, key: str, default: Any = None) -> Any:
    return source.get(key, default) if isinstance(source, Mapping) else getattr(source, key, default)


def get_subscription_definitions() -> tuple[SubscriptionDefinition, ...]:
    """Fail the complete panel closed on duplicate/ambiguous configured IDs."""
    definitions: list[SubscriptionDefinition] = []
    source_ids: set[str] = set()
    role_ids: set[int] = set()
    try:
        sources = _source_supplier()
        for index, source in enumerate(sources):
            if index >= MAX_SOURCES:
                raise SubscriptionConfigurationError("Too many configured subscription sources.")
            source_id = _value(source, "source_id") or _value(source, "id")
            if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", source_id):
                raise SubscriptionConfigurationError("A source ID is missing or invalid.")
            if source_id in source_ids:
                raise SubscriptionConfigurationError("Duplicate source IDs are configured.")
            source_ids.add(source_id)
            enabled = _value(source, "enabled", True)
            if type(enabled) is not bool:
                raise SubscriptionConfigurationError("A source enabled value is invalid.")
            if not enabled:
                continue
            role_id = _value(source, "role_id")
            if role_id is None:
                continue  # A source without an optional subscription role is valid.
            channel_id = _value(source, "channel_id")
            if type(role_id) is not int or role_id <= 0:
                raise SubscriptionConfigurationError("A subscription role ID is invalid.")
            if type(channel_id) is not int or channel_id <= 0:
                raise SubscriptionConfigurationError("A subscription channel ID is invalid.")
            if role_id in role_ids:
                raise SubscriptionConfigurationError("A subscription role is assigned to multiple enabled sources.")
            role_ids.add(role_id)
            name = _value(source, "name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise SubscriptionConfigurationError("A subscription display name is invalid.")
            definitions.append(SubscriptionDefinition(source_id, name.strip(), channel_id, role_id))
    except SubscriptionConfigurationError:
        raise
    except Exception:
        # Supplier errors can contain URLs/private provider paths. Never show them.
        raise SubscriptionConfigurationError("Subscription configuration could not be read.") from None
    return tuple(definitions)


def _target_error(guild: discord.Guild | None) -> str | None:
    if guild is None or _target_guild_id <= 0 or guild.id != _target_guild_id:
        return "Subscriptions are available only in Herald's configured server."
    return None


def _dangerous(permissions: discord.Permissions) -> bool:
    return bool(
        permissions.value & ~discord.Permissions.all().value
        or any(getattr(permissions, flag, False) for flag in DANGEROUS_PERMISSIONS)
        or any(enabled and name.startswith("manage_") for name, enabled in permissions)
    )


def validate_subscription_role(
    guild: discord.Guild,
    definition: SubscriptionDefinition,
) -> tuple[discord.Role | None, str | None]:
    """Recheck the target, live guild role, hierarchy, and privilege grants."""
    target_error = _target_error(guild)
    if target_error:
        return None, target_error
    role = guild.get_role(definition.role_id)
    if role is None or role.guild.id != guild.id:
        return None, "A configured subscription role is missing. Please tell the server owner."
    channel = guild.get_channel(definition.channel_id)
    if channel is None or channel.guild.id != guild.id:
        return None, "A configured subscription channel is missing. Please tell the server owner."
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return None, "Herald needs Manage Roles permission. Please tell the server owner."
    if role.is_default() or role.managed or role >= me.top_role:
        return None, "The configured role cannot be managed safely. Please tell the server owner."
    if STAFF_ROLE_NAME.search(role.name) or _dangerous(role.permissions):
        return None, "Privileged or staff roles cannot be used for subscriptions."
    for guild_channel in guild.channels:
        allowed, _ = guild_channel.overwrites_for(role).pair()
        if _dangerous(allowed):
            return None, "A subscription role has privileged channel permissions. Please tell the server owner."
    return role, None


def subscription_diagnostics(guild: discord.Guild | None) -> list[dict[str, str]]:
    error = _target_error(guild)
    if error:
        return [{"status": "FAIL", "check": "subscription target", "detail": error}]
    try:
        definitions = get_subscription_definitions()
    except SubscriptionConfigurationError as exc:
        return [{"status": "FAIL", "check": "subscription configuration", "detail": str(exc)}]
    if not definitions:
        return [{"status": "WARN", "check": "subscription roles", "detail": "No enabled subscription roles are configured."}]
    results = []
    for definition in definitions:
        _, error = validate_subscription_role(guild, definition)
        results.append({
            "status": "FAIL" if error else "PASS",
            "check": f"subscription role {definition.role_id}",
            "detail": error or "Role, channel, hierarchy and permission checks passed.",
        })
    return results


def _display_name(name: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(name))


def make_subscription_embed(guild: discord.Guild | None = None, page: int = 0) -> discord.Embed:
    embed = discord.Embed(
        title="Herald Alert Subscriptions",
        description="Choose an alert from the menu to subscribe or unsubscribe. You can change your choices at any time.",
        color=0xD4AF37,
    )
    if _target_error(guild):
        embed.description = _target_error(guild)
        return embed
    try:
        definitions = get_subscription_definitions()
    except SubscriptionConfigurationError:
        embed.description = "Subscription configuration needs the server owner's attention."
        return embed
    selected = definitions[max(0, page) * PAGE_SIZE:(max(0, page) + 1) * PAGE_SIZE]
    if not selected:
        embed.description = "No enabled subscription roles are configured on this page."
    for definition in selected:
        embed.add_field(
            name=_display_name(definition.name),
            value=f"Alert channel: <#{definition.channel_id}>",
            inline=False,
        )
    embed.set_footer(text=f"Herald · opt-in alerts · page {page + 1}")
    return embed


def member_subscription_text(member: discord.Member) -> str:
    error = _target_error(member.guild)
    if error:
        return error
    try:
        definitions = get_subscription_definitions()
    except SubscriptionConfigurationError:
        return "Subscription configuration needs the server owner's attention."
    lines = ["**Your Herald subscriptions**", ""]
    if not definitions:
        return "No enabled subscription roles are configured."
    role_ids = {role.id for role in member.roles}
    for index, definition in enumerate(definitions):
        mark = "✅" if definition.role_id in role_ids else "❌"
        line = f"{mark} {_display_name(definition.name)}"
        if sum(map(len, lines)) + len(lines) + len(line) > 1750:
            lines.append(f"… and {len(definitions) - index} more; use the other subscription panels.")
            break
        lines.append(line)
    return "\n".join(lines)


async def _reply(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(
        text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )


def _interaction_error(interaction: discord.Interaction) -> str | None:
    error = _target_error(interaction.guild)
    if error:
        return error
    if not isinstance(interaction.user, discord.Member) or interaction.user.guild.id != _target_guild_id:
        return "Subscriptions require membership in Herald's configured server."
    return None


class SubscriptionSelect(discord.ui.Select):
    def __init__(self, definitions: Iterable[SubscriptionDefinition], page: int = 0):
        options = [discord.SelectOption(label=definition.name, value=definition.token) for definition in definitions]
        super().__init__(
            placeholder="Choose an alert to subscribe / unsubscribe",
            min_values=1, max_values=1,
            # The placeholder is used only for persistent callback registration.
            # Old message values are still checked against current source policy.
            options=options or [discord.SelectOption(label="No configured alerts", value="unavailable")],
            disabled=not bool(options),
            custom_id=f"herald_subscribe:v020:select:{page}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        error = _interaction_error(interaction)
        if error:
            await _reply(interaction, error)
            return
        try:
            definitions = get_subscription_definitions()
        except SubscriptionConfigurationError:
            await _reply(interaction, "Subscription configuration needs the server owner's attention.")
            return
        # values come from the interaction payload, not from a trusted UI snapshot.
        selected = self.values
        definition = next((item for item in definitions if len(selected) == 1 and item.token == selected[0]), None)
        if definition is None:
            await _reply(interaction, "That subscription changed or is disabled. Ask the server owner for a fresh panel.")
            return
        role, error = validate_subscription_role(interaction.guild, definition)
        if error:
            await _reply(interaction, error)
            return
        try:
            if role.id in {member_role.id for member_role in interaction.user.roles}:
                await interaction.user.remove_roles(role, reason="Herald subscription opt-out")
                result = "unsubscribed from"
            else:
                await interaction.user.add_roles(role, reason="Herald subscription opt-in")
                result = "subscribed to"
            await _reply(interaction, f"You are now {result} **{_display_name(definition.name)}** alerts.")
        except discord.Forbidden:
            await _reply(interaction, "Discord blocked the role change. Ask the server owner to check Herald's permissions and role order.")
        except discord.HTTPException:
            await _reply(interaction, "Discord could not confirm the role change. Check your roles before trying again.")


class SubscriptionButton(discord.ui.Button):
    """v0.1.1 compatibility handler: existing panels can never grant roles."""
    def __init__(self, sub_key: str):
        super().__init__(
            label=LEGACY_SUBSCRIPTIONS[sub_key],
            style=discord.ButtonStyle.secondary,
            custom_id=f"herald_subscribe:{sub_key}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        error = _interaction_error(interaction)
        await _reply(interaction, error or "This old subscription panel has been retired. Ask the server owner to post a new /herald setup subscriptions panel.")


class ShowMySubscriptionsButton(discord.ui.Button):
    def __init__(self, *, legacy: bool = False):
        super().__init__(
            label="Show My Alerts", style=discord.ButtonStyle.primary,
            custom_id="herald_subscribe:show_mine" if legacy else "herald_subscribe:v020:show_mine",
        )
        self.legacy = legacy

    async def callback(self, interaction: discord.Interaction) -> None:
        error = _interaction_error(interaction)
        if error:
            await _reply(interaction, error)
        elif self.legacy:
            await _reply(interaction, "This old panel has been retired. Ask the server owner for a new subscription panel.")
        else:
            await _reply(interaction, member_subscription_text(interaction.user))


class SubscriptionView(discord.ui.View):
    def __init__(self, *, register_all: bool = False, page: int = 0, register_empty: bool = False):
        super().__init__(timeout=None)
        if register_all:
            for key in LEGACY_SUBSCRIPTIONS:
                self.add_item(SubscriptionButton(key))
            self.add_item(ShowMySubscriptionsButton(legacy=True))
            return
        definitions = get_subscription_definitions()
        selected = definitions[max(0, page) * PAGE_SIZE:(max(0, page) + 1) * PAGE_SIZE]
        if selected or register_empty:
            self.add_item(SubscriptionSelect(selected, page))
        self.add_item(ShowMySubscriptionsButton())


def subscription_views() -> list[SubscriptionView]:
    """Post/register all current pages; every component has a stable persistent ID."""
    count = len(get_subscription_definitions())
    return [SubscriptionView(page=page) for page in range(max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE))]
