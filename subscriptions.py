from __future__ import annotations

import discord

from config import (
    FREE_GAMES_CHANNEL_NAME,
    GPU_UPDATES_CHANNEL_NAME,
    STREAM_ALERTS_CHANNEL_NAME,
    SECURITY_ALERTS_CHANNEL_NAME,
)


SUBSCRIPTION_DEFS = {
    "free-games": {
        "label": "Free Games",
        "role_name": "Free Games",
        "emoji": "🎮",
        "channel_name": FREE_GAMES_CHANNEL_NAME,
        "description": "Free game giveaways and limited-time claims.",
    },
    "gpu-updates": {
        "label": "GPU Updates",
        "role_name": "GPU Updates",
        "emoji": "🖥️",
        "channel_name": GPU_UPDATES_CHANNEL_NAME,
        "description": "Graphics driver updates and related GPU news.",
    },
    "stream-alerts": {
        "label": "Stream Alerts",
        "role_name": "Stream Alerts",
        "emoji": "📡",
        "channel_name": STREAM_ALERTS_CHANNEL_NAME,
        "description": "Live-stream and creator alerts.",
    },
    "security-alerts": {
        "label": "Security Alerts",
        "role_name": "Security Alerts",
        "emoji": "🛡️",
        "channel_name": SECURITY_ALERTS_CHANNEL_NAME,
        "description": "Security advisories, breach warnings, and major vulnerability notices.",
    },
}


def _find_role(guild: discord.Guild, role_name: str) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=role_name)


def _find_channel(
    guild: discord.Guild,
    channel_name: str,
) -> discord.abc.GuildChannel | None:
    return discord.utils.get(guild.channels, name=channel_name)


def make_subscription_embed(guild: discord.Guild | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="🎺 Herald Angel Alert Subscriptions",
        description=(
            "Choose which Herald alert channels you want to see.\n\n"
            "Click a button below to subscribe or unsubscribe. "
            "You can change your choices at any time."
        ),
        color=0xD4AF37,
    )

    for info in SUBSCRIPTION_DEFS.values():
        channel_text = f"#{info['channel_name']}"

        if guild:
            channel = _find_channel(guild, info["channel_name"])
            if channel:
                channel_text = channel.mention

        embed.add_field(
            name=f"{info['emoji']} {info['label']}",
            value=f"{info['description']}\nChannel: {channel_text}",
            inline=False,
        )

    embed.set_footer(
        text="Herald Angel · opt-in alerts · no @everyone pings by default"
    )
    return embed


def member_subscription_text(member: discord.Member) -> str:
    lines = ["🎺 **Your Herald subscriptions**", ""]

    for info in SUBSCRIPTION_DEFS.values():
        role = _find_role(member.guild, info["role_name"])
        has_role = bool(role and role in member.roles)
        mark = "✅" if has_role else "❌"
        lines.append(f"{mark} {info['emoji']} **{info['label']}**")

    return "\n".join(lines)


class SubscriptionButton(discord.ui.Button):
    def __init__(self, sub_key: str):
        info = SUBSCRIPTION_DEFS[sub_key]

        super().__init__(
            label=info["label"],
            emoji=info["emoji"],
            style=discord.ButtonStyle.secondary,
            custom_id=f"herald_subscribe:{sub_key}",
        )

        self.sub_key = sub_key

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "🎺 Subscription buttons only work inside the server.",
                ephemeral=True,
            )
            return

        info = SUBSCRIPTION_DEFS[self.sub_key]
        role = _find_role(interaction.guild, info["role_name"])

        if role is None:
            await interaction.response.send_message(
                f"⚠️ I could not find the **{info['role_name']}** role. Please tell a server admin.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me

        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "⚠️ I do not have **Manage Roles** permission yet. Please tell a server admin.",
                ephemeral=True,
            )
            return

        if role >= me.top_role:
            await interaction.response.send_message(
                f"⚠️ I cannot manage the **{role.name}** role because it is above or equal to my bot role.",
                ephemeral=True,
            )
            return

        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(
                    role,
                    reason="Herald Angel subscription button unsubscribe",
                )

                await interaction.response.send_message(
                    f"🎺 You are now unsubscribed from **{info['label']}** alerts.",
                    ephemeral=True,
                )
                return

            await interaction.user.add_roles(
                role,
                reason="Herald Angel subscription button subscribe",
            )

            channel = _find_channel(interaction.guild, info["channel_name"])
            channel_text = channel.mention if channel else f"#{info['channel_name']}"

            await interaction.response.send_message(
                f"🎺 You are now subscribed to **{info['label']}** alerts.\n"
                f"You should now be able to see {channel_text}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ Discord blocked me from changing that role. "
                "Please check my role order and Manage Roles permission.",
                ephemeral=True,
            )

        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"⚠️ Discord returned an error while changing that role: `{exc}`",
                ephemeral=True,
            )


class ShowMySubscriptionsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Show My Alerts",
            emoji="📋",
            style=discord.ButtonStyle.primary,
            custom_id="herald_subscribe:show_mine",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "🎺 Subscription status only works inside the server.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            member_subscription_text(interaction.user),
            ephemeral=True,
        )


class SubscriptionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(SubscriptionButton("free-games"))
        self.add_item(SubscriptionButton("gpu-updates"))
        self.add_item(SubscriptionButton("stream-alerts"))
        self.add_item(SubscriptionButton("security-alerts"))
        self.add_item(ShowMySubscriptionsButton())