import asyncio
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import discord

from subscriptions import (
    SubscriptionView,
    make_subscription_embed,
)

from config import (
    DISCORD_TOKEN,
    OWNER_ID,
    HERALD_GUILD_ID,
    HERALD_NAME,
    HERALD_COMMAND_PREFIX,
    HERALD_DM_COMMANDS_ENABLED,
    HERALD_OWNER_ONLY,
    HERALD_REPLY_TO_NON_OWNER_DMS,
    HERALD_SERVER_COMMANDS_ENABLED,
    HERALD_EMOJIS,
    HERALD_AUTO_POST_ENABLED,
    HERALD_CHECK_SECONDS,
    HERALD_STARTUP_BACKLOG_MODE,
    HERALD_POST_BATCH_LIMIT,
    HERALD_DELIVERY_MAX_ATTEMPTS,
    WELCOME_ENABLED,
    WELCOME_CHANNEL_NAME,
    SUBSCRIPTIONS_CHANNEL_NAME,
    FREE_GAMES_CHANNEL_NAME,
    FREE_GAMES_ENABLED,
    GPU_UPDATES_CHANNEL_NAME,
    GPU_UPDATES_ENABLED,
    STREAM_ALERTS_CHANNEL_NAME,
    SECURITY_ALERTS_CHANNEL_NAME,
    SECURITY_ENABLED,
    TWITCH_ENABLED,
    TWITCH_PING_ROLE_ENABLED,
    TWITCH_PING_ROLE_NAME,
)
from storage import (
    init_db,
    log_event,
    count_events,
    count_audit_events,
    count_items_by_status,
    audit_event,
    audit_item_text,
    audit_recent_text,
    audit_summary_text,
    audit_verify_text,
)
from watchers import (
    discover_items,
    failed_items_text,
    format_discover_stats,
    format_skip_stats,
    get_item,
    held_items,
    held_items_text,
    mark_failed,
    mark_posted,
    pending_items,
    pending_items_text,
    posted_items_text,
    promote_item,
    retry_failed,
    skip_items,
    skip_range,
    skip_held_items,
    startup_mode_to_status,
    watcher_status_text,
)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

watcher_loop_started = False
persistent_view_registered = False

WATCHER_RUNTIME = {
    "running": False,
    "first_run_complete": False,
    "last_poll": "",
    "last_success": "",
    "last_error": "",
    "last_provider_errors": [],
    "last_discovery": {},
    "last_delivery": {},
}

DM_CLEAN_DEFAULT_LIMIT = 100
DM_CLEAN_MAX_LIMIT = 250


def emoji(name: str) -> str:
    return HERALD_EMOJIS.get(name, "")


def is_owner(user: discord.abc.User) -> bool:
    return int(user.id) == int(OWNER_ID)


def clean_prompt(message: discord.Message) -> str:
    content = (message.content or "").strip()

    if client.user:
        content = content.replace(f"<@{client.user.id}>", "")
        content = content.replace(f"<@!{client.user.id}>", "")

    return " ".join(content.split()).strip()


def command_body(prompt: str) -> str | None:
    prompt_clean = prompt.strip()

    if not prompt_clean:
        return None

    lower = prompt_clean.lower()

    if lower == HERALD_COMMAND_PREFIX:
        return ""

    prefix_with_space = HERALD_COMMAND_PREFIX + " "

    if lower.startswith(prefix_with_space):
        return prompt_clean[len(prefix_with_space):].strip()

    return None


def find_text_channel_by_name(guild: discord.Guild, name: str):
    wanted = name.strip().lower()

    for channel in guild.text_channels:
        if channel.name.lower() == wanted:
            return channel

    return None


def configured_guild() -> discord.Guild | None:
    if HERALD_GUILD_ID > 0:
        return client.get_guild(HERALD_GUILD_ID)

    if len(client.guilds) == 1:
        return client.guilds[0]

    return None


def configured_guild_error() -> str:
    if not client.guilds:
        return "I am not connected to any Discord server yet."

    if HERALD_GUILD_ID > 0:
        return (
            f"Configured HERALD_GUILD_ID `{HERALD_GUILD_ID}` is not currently available."
        )

    return (
        "I am connected to more than one server. Set HERALD_GUILD_ID in .env "
        "so alerts cannot be posted to the wrong server."
    )


async def post_subscription_panel(target_channel: discord.TextChannel) -> None:
    await target_channel.send(
        embed=make_subscription_embed(target_channel.guild),
        view=SubscriptionView(),
    )


def build_welcome_message(member: discord.Member) -> str:
    return (
        f"{emoji('herald')} Welcome to **{member.guild.name}**, {member.mention}! "
        "Make yourself comfy."
    )


async def post_welcome_for_member(
    member: discord.Member,
    event_type: str = "member_join_welcome",
) -> tuple[bool, str]:
    if not WELCOME_ENABLED:
        return False, "Welcome messages are disabled."

    channel = find_text_channel_by_name(member.guild, WELCOME_CHANNEL_NAME)

    if channel is None:
        return False, f"Welcome channel #{WELCOME_CHANNEL_NAME} not found in {member.guild.name}."

    try:
        await channel.send(
            build_welcome_message(member),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                users=[member],
                roles=False,
                replied_user=False,
            ),
        )

        log_event(
            event_type,
            guild_id=str(member.guild.id),
            channel_id=str(channel.id),
            user_id=str(member.id),
            detail=f"Welcomed {member} in #{channel.name}",
        )

        return True, f"Posted welcome message in #{channel.name}."

    except discord.Forbidden:
        return False, f"Missing permission to send welcome in #{channel.name}."

    except Exception as e:
        return False, f"Welcome failed: {e}"


async def clean_dm_messages(message: discord.Message, limit: int = DM_CLEAN_DEFAULT_LIMIT):
    if not isinstance(message.channel, discord.DMChannel):
        await message.channel.send("DM cleanup only works in Herald DMs.")
        return

    try:
        limit = int(limit)
    except Exception:
        limit = DM_CLEAN_DEFAULT_LIMIT

    if limit <= 0:
        limit = DM_CLEAN_DEFAULT_LIMIT

    if limit > DM_CLEAN_MAX_LIMIT:
        limit = DM_CLEAN_MAX_LIMIT

    deleted = 0
    scanned = 0
    failed = 0

    # Add a small buffer because the owner's messages cannot normally be deleted
    # by the bot, so we may need to scan more than the requested count.
    scan_limit = min(limit * 2, DM_CLEAN_MAX_LIMIT * 2)

    async for old_message in message.channel.history(limit=scan_limit):
        scanned += 1

        if client.user and old_message.author.id != client.user.id:
            continue

        try:
            await old_message.delete()
            deleted += 1
            await asyncio.sleep(1)
        except Exception:
            failed += 1

        if deleted >= limit:
            break

    confirmation = await message.channel.send(
        f"{emoji('herald')} Cleaned `{deleted}` Herald DM message(s). "
        f"Scanned `{scanned}`."
        + (f" Failed: `{failed}`." if failed else "")
    )

    # Auto-remove the confirmation too after a few seconds, so cleanup stays clean.
    try:
        await asyncio.sleep(5)
        await confirmation.delete()
    except discord.HTTPException:
        return

async def send_long(channel, text: str):
    text = text or ""

    if len(text) <= 1900:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        return

    chunks = []

    while text:
        chunks.append(text[:1900])
        text = text[1900:]

    for chunk in chunks[:4]:
        await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RAW_MENTION_RE = re.compile(r"<(@!?|@&|#)(\d+)>")


def sanitize_discord_text(value: str, max_chars: int = 500) -> str:
    text = str(value or "")
    text = _CONTROL_CHARS_RE.sub(" ", text)
    text = text.replace("@everyone", "@\u200beveryone")
    text = text.replace("@here", "@\u200bhere")
    text = _RAW_MENTION_RE.sub(
        lambda match: f"<{match.group(1)}\u200b{match.group(2)}>",
        text,
    )
    text = " ".join(text.split()).strip()

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text


def safe_http_url(value: str, max_chars: int = 1000) -> str:
    url = str(value or "").strip()
    url = _CONTROL_CHARS_RE.sub("", url)

    if len(url) > max_chars:
        url = url[:max_chars]

    if not url.lower().startswith(("https://", "http://")):
        return ""

    return url


def sanitized_item_for_post(item: dict) -> dict:
    cleaned = dict(item or {})

    text_limits = {
        "title": 256,
        "source": 120,
        "summary": 3500,
        "published_at": 100,
        "tags": 900,
    }

    for key, max_chars in text_limits.items():
        cleaned[key] = sanitize_discord_text(cleaned.get(key) or "", max_chars)

    cleaned["url"] = safe_http_url(cleaned.get("url") or "")
    cleaned["image_url"] = safe_http_url(cleaned.get("image_url") or "")

    return cleaned    


def format_published_at(value: str) -> str:
    value = (value or "").strip()

    if not value:
        return ""

    dt = None

    try:
        raw = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except Exception:
        dt = None

    if dt is None:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            dt = None

    if dt is None:
        return value[:100]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%d %b %Y, %H:%M UTC")


def build_free_game_embed(item: dict) -> discord.Embed:
    title = item.get("title") or "Free game"
    url = item.get("url") or ""
    summary = item.get("summary") or ""

    embed = discord.Embed(
        title=title[:256],
        url=url or None,
        description=summary[:3500] if summary else "Free game / giveaway discovered by Herald Angel.",
    )

    embed.set_author(name="GamerPower")

    image_url = (item.get("image_url") or "").strip()

    if image_url:
        embed.set_thumbnail(url=image_url)

    return embed


def build_gpu_update_embed(item: dict) -> discord.Embed:
    title = item.get("title") or "GPU driver update"
    url = item.get("url") or ""
    summary = item.get("summary") or ""

    embed = discord.Embed(
        title=title[:256],
        url=url or None,
        description=summary[:3500] if summary else "GPU driver update discovered by Herald Angel.",
    )

    embed.add_field(
        name="Source",
        value=item.get("source") or "Guru3D RSS",
        inline=True,
    )

    published = format_published_at(item.get("published_at") or "")

    if published:
        embed.add_field(
            name="Published",
            value=published,
            inline=True,
        )

    embed.set_footer(text="Guru3D RSS watcher")
    return embed

def build_stream_alert_embed(item: dict) -> discord.Embed:
    title = item.get("title") or "Twitch stream live"
    url = item.get("url") or ""
    summary = item.get("summary") or ""
    source = item.get("source") or "Twitch"

    embed = discord.Embed(
        title=title[:256],
        url=url or None,
        description=summary[:3500] if summary else "A watched Twitch channel is live.",
    )

    embed.add_field(
        name="Source",
        value=source,
        inline=True,
    )

    published = format_published_at(item.get("published_at") or "")

    if published:
        embed.add_field(
            name="Started",
            value=published,
            inline=True,
        )

    image_url = (item.get("image_url") or "").strip()

    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(text="Twitch live watcher")
    return embed

def build_security_alert_embed(item: dict) -> discord.Embed:
    title = item.get("title") or "Security alert"
    url = item.get("url") or ""
    summary = item.get("summary") or ""
    source = item.get("source") or "Security RSS"
    tags = item.get("tags") or ""

    embed = discord.Embed(
        title=title[:256],
        url=url or None,
        description=summary[:3500] if summary else "Security alert discovered by Herald Angel.",
    )

    embed.add_field(
        name="Source",
        value=source,
        inline=True,
    )

    published = format_published_at(item.get("published_at") or "")

    if published:
        embed.add_field(
            name="Published",
            value=published,
            inline=True,
        )

    if tags:
        embed.add_field(
            name="Tags",
            value=str(tags)[:900],
            inline=False,
        )

    embed.set_footer(text="Security RSS watcher · review-first")
    return embed

def target_channel_name_for_item(item: dict) -> str:
    category = (item.get("category") or "").strip().lower()

    if category == "free_games":
        return FREE_GAMES_CHANNEL_NAME

    if category == "gpu_updates":
        return GPU_UPDATES_CHANNEL_NAME

    if category == "stream_alerts":
        return STREAM_ALERTS_CHANNEL_NAME

    if category == "security_alerts":
        return SECURITY_ALERTS_CHANNEL_NAME

    return ""

def find_role_by_name(
    guild: discord.Guild,
    role_name: str,
) -> discord.Role | None:
    wanted = (role_name or "").strip().lower()

    if not wanted:
        return None

    for role in guild.roles:
        if role.name.lower() == wanted:
            return role

    return None


def find_role_mention_by_name(
    guild: discord.Guild,
    role_name: str,
) -> str:
    role = find_role_by_name(guild, role_name)
    return role.mention if role else ""


def allowed_mentions_for_item(
    item: dict,
    guild: discord.Guild,
) -> discord.AllowedMentions:
    category = (item.get("category") or "").strip().lower()

    # Only Twitch stream alerts may intentionally ping the configured alert role.
    # Resolve the role inside the same guild as the destination channel.
    if category == "stream_alerts" and TWITCH_PING_ROLE_ENABLED:
        role = find_role_by_name(guild, TWITCH_PING_ROLE_NAME)

        if role:
            return discord.AllowedMentions(
                everyone=False,
                users=False,
                roles=[role],
                replied_user=False,
            )

    return discord.AllowedMentions.none()

def build_post_payload_for_item(
    item: dict,
    guild: discord.Guild,
) -> tuple[str, discord.Embed]:
    item = sanitized_item_for_post(item)
    category = (item.get("category") or "").strip().lower()
    title = item.get("title") or "Herald item"
    source = item.get("source") or "unknown source"
    url = item.get("url") or ""

    if category == "free_games":
        content = (
            f"{emoji('free_game')} **Free Game Found!**\n\n"
            f"**{title}**\n"
            f"Source: {source}\n"
            f"{url}"
        )
        return content, build_free_game_embed(item)

    if category == "gpu_updates":
        content = f"{emoji('gcard')} **Graphics Driver Update**"
        return content, build_gpu_update_embed(item)

    if category == "stream_alerts":
        role_mention = ""

        if TWITCH_PING_ROLE_ENABLED:
            role_mention = find_role_mention_by_name(guild, TWITCH_PING_ROLE_NAME)

        prefix = f"{role_mention}\n" if role_mention else ""

        content = (
            f"{prefix}📡 **Twitch Stream Live!**\n\n"
            f"**{title}**\n"
            f"{url}"
        )
        return content, build_stream_alert_embed(item)

    if category == "security_alerts":
        content = f"{emoji('security')} **Security Alert**"
        return content, build_security_alert_embed(item)

    embed = discord.Embed(
        title=title[:256],
        url=url or None,
        description=(item.get("summary") or "Herald item.")[:3500],
    )

    if source:
        embed.add_field(name="Source", value=source, inline=True)

    published = format_published_at(item.get("published_at") or "")

    if published:
        embed.add_field(name="Published", value=published, inline=True)

    return f"{emoji('herald')} **Herald Update**", embed


async def post_item_to_discord(item: dict) -> tuple[bool, str]:
    channel_name = target_channel_name_for_item(item)

    if not channel_name:
        return False, f"No target channel configured for category `{item.get('category')}`."

    guild = configured_guild()

    if guild is None:
        return False, configured_guild_error()

    target_channel = find_text_channel_by_name(guild, channel_name)

    if target_channel is None:
        return False, f"Could not find target channel `#{channel_name}`."

    content, embed = build_post_payload_for_item(item, target_channel.guild)

    try:
        sent = await target_channel.send(
            content=content[:1900],
            embed=embed,
            allowed_mentions=allowed_mentions_for_item(item, target_channel.guild),
        )
        return True, str(sent.id)

    except discord.Forbidden:
        return False, f"Missing permission to post in `#{channel_name}`."

    except Exception as e:
        return False, str(e)


async def deliver_pending_items_once(limit: int = HERALD_POST_BATCH_LIMIT) -> dict:
    stats = {
        "checked": 0,
        "posted": 0,
        "failed": 0,
        "missing": 0,
    }

    items = pending_items(limit)

    for item in items:
        stats["checked"] += 1
        item_id = int(item.get("id") or 0)

        if not item_id:
            stats["missing"] += 1
            continue

        ok, detail = await post_item_to_discord(item)

        if ok:
            mark_posted(item_id, detail)
            stats["posted"] += 1
        else:
            mark_failed(item_id, detail)
            stats["failed"] += 1

    WATCHER_RUNTIME["last_delivery"] = stats
    return stats


async def post_held_items_once(limit: int = 5, max_limit: int = 20) -> dict:
    limit = int(limit)

    if limit <= 0:
        return {
            "checked": 0,
            "posted": 0,
            "failed": 0,
            "missing": 0,
            "error": "Limit must be greater than zero.",
        }

    if limit > max_limit:
        return {
            "checked": 0,
            "posted": 0,
            "failed": 0,
            "missing": 0,
            "error": f"Safety cap is {max_limit} held items at once.",
        }

    stats = {
        "checked": 0,
        "posted": 0,
        "failed": 0,
        "missing": 0,
        "error": "",
    }

    items = held_items(limit)

    for item in items:
        stats["checked"] += 1
        item_id = int(item.get("id") or 0)

        if not item_id:
            stats["missing"] += 1
            continue

        ok, detail = await post_item_to_discord(item)

        if ok:
            mark_posted(item_id, detail)
            stats["posted"] += 1
        else:
            mark_failed(item_id, detail)
            stats["failed"] += 1

    return stats


async def run_watcher_cycle(first_run: bool = False) -> dict:
    if first_run:
        queue_status = startup_mode_to_status(HERALD_STARTUP_BACKLOG_MODE)
    else:
        queue_status = "pending"

    discovery = await asyncio.to_thread(discover_items, queue_status)
    WATCHER_RUNTIME["last_discovery"] = discovery
    WATCHER_RUNTIME["last_provider_errors"] = list(discovery.get("errors") or [])

    delivery = {
        "checked": 0,
        "posted": 0,
        "failed": 0,
        "missing": 0,
        "requeued": 0,
    }

    if HERALD_AUTO_POST_ENABLED:
        requeued = await asyncio.to_thread(
            retry_failed,
            HERALD_DELIVERY_MAX_ATTEMPTS,
        )
        delivery = await deliver_pending_items_once(HERALD_POST_BATCH_LIMIT)
        delivery["requeued"] = requeued

    WATCHER_RUNTIME["last_delivery"] = delivery

    return {
        "discovery": discovery,
        "delivery": delivery,
        "queue_status": queue_status,
    }


async def run_watchers_loop():
    print("Herald watcher loop started.", flush=True)

    first_run = True
    WATCHER_RUNTIME["running"] = True

    while not client.is_closed():
        try:
            WATCHER_RUNTIME["last_poll"] = now_text()

            result = await run_watcher_cycle(first_run=first_run)

            WATCHER_RUNTIME["last_success"] = now_text()
            WATCHER_RUNTIME["last_error"] = ""
            WATCHER_RUNTIME["first_run_complete"] = True

            provider_errors = WATCHER_RUNTIME.get("last_provider_errors") or []

            if provider_errors:
                print(
                    "Herald provider errors: " + " | ".join(provider_errors[:5]),
                    flush=True,
                )

            print(
                "Herald watcher cycle complete: "
                f"queue={result.get('queue_status')} "
                f"created={result.get('discovery', {}).get('created', 0)} "
                f"requeued={result.get('delivery', {}).get('requeued', 0)} "
                f"posted={result.get('delivery', {}).get('posted', 0)} "
                f"failed={result.get('delivery', {}).get('failed', 0)}",
                flush=True,
            )

            first_run = False

        except Exception as e:
            WATCHER_RUNTIME["last_error"] = str(e)
            print(f"Herald watcher loop error: {e}", flush=True)

        await asyncio.sleep(HERALD_CHECK_SECONDS)

    WATCHER_RUNTIME["running"] = False


def runtime_status_text() -> str:
    discovery = WATCHER_RUNTIME.get("last_discovery") or {}
    delivery = WATCHER_RUNTIME.get("last_delivery") or {}
    provider_errors = WATCHER_RUNTIME.get("last_provider_errors") or []
    provider_error_text = " | ".join(provider_errors[:3]) if provider_errors else "none"

    return (
        f"{emoji('herald')} **Herald Runtime**\n\n"
        f"Watcher loop running: `{WATCHER_RUNTIME.get('running')}`\n"
        f"Auto-post enabled: `{HERALD_AUTO_POST_ENABLED}`\n"
        f"Check interval: `{HERALD_CHECK_SECONDS}s`\n"
        f"Startup backlog mode: `{HERALD_STARTUP_BACKLOG_MODE}`\n"
        f"First run complete: `{WATCHER_RUNTIME.get('first_run_complete')}`\n"
        f"Last poll: `{WATCHER_RUNTIME.get('last_poll') or 'never'}`\n"
        f"Last success: `{WATCHER_RUNTIME.get('last_success') or 'never'}`\n"
        f"Last error: `{WATCHER_RUNTIME.get('last_error') or 'none'}`\n"
        f"Last provider errors: `{provider_error_text}`\n\n"
        f"Last discovery created: `{discovery.get('created', 0)}`\n"
        f"Last discovery existing: `{discovery.get('existing', 0)}`\n"
        f"Last delivery requeued: `{delivery.get('requeued', 0)}`\n"
        f"Last delivery checked: `{delivery.get('checked', 0)}`\n"
        f"Last delivery posted: `{delivery.get('posted', 0)}`\n"
        f"Last delivery failed: `{delivery.get('failed', 0)}`"
    )


async def handle_status(message: discord.Message):
    guild_names = ", ".join(guild.name for guild in client.guilds) or "none"
    target_guild = configured_guild()
    target_guild_text = (
        f"{target_guild.name} (`{target_guild.id}`)"
        if target_guild
        else f"unresolved — {configured_guild_error()}"
    )

    reply = (
        f"{emoji('herald')} **{HERALD_NAME} Status**\n\n"
        f"Online: `yes`\n"
        f"Guilds: `{len(client.guilds)}` — {guild_names}\n"
        f"Target guild: {target_guild_text}\n"
        f"Configured guild ID: `{HERALD_GUILD_ID or 'automatic-single-guild'}`\n"
        f"Owner-only: `{HERALD_OWNER_ONLY}`\n"
        f"DM commands: `{HERALD_DM_COMMANDS_ENABLED}`\n"
        f"Server commands: `{HERALD_SERVER_COMMANDS_ENABLED}`\n"
        f"Welcome enabled: `{WELCOME_ENABLED}`\n"
        f"Welcome channel: `#{WELCOME_CHANNEL_NAME}`\n\n"
        f"Modules / channels:\n"
        f"- Free games: `{FREE_GAMES_ENABLED}` — `#{FREE_GAMES_CHANNEL_NAME}`\n"
        f"- GPU updates: `{GPU_UPDATES_ENABLED}` — `#{GPU_UPDATES_CHANNEL_NAME}`\n"
        f"- Twitch alerts: `{TWITCH_ENABLED}` — `#{STREAM_ALERTS_CHANNEL_NAME}`\n"
        f"- Security alerts: `{SECURITY_ENABLED}` — `#{SECURITY_ALERTS_CHANNEL_NAME}`\n\n"
        f"DB events logged: `{count_events()}`\n"
        f"Audit events logged: `{count_audit_events()}`\n"
        f"Held items: `{count_items_by_status('held')}`\n"
        f"Pending items: `{count_items_by_status('pending')}`\n"
        f"Failed items: `{count_items_by_status('failed')}`"
    )

    await send_long(message.channel, reply)


async def handle_help(message: discord.Message):
    reply = (
        f"{emoji('herald')} **{HERALD_NAME} Commands**\n\n"
        f"`{HERALD_COMMAND_PREFIX} status`\n"
        "Show Herald status and configured channels.\n\n"
        f"`{HERALD_COMMAND_PREFIX} welcome test`\n"
        "Send a test welcome message to the configured welcome channel.\n\n"
        f"`{HERALD_COMMAND_PREFIX} runtime`\n"
        "Show auto watcher runtime state.\n\n"
        f"`{HERALD_COMMAND_PREFIX} subs panel`\n"
        "Post the Herald alert subscription button panel in the configured subscriptions channel.\n\n"
        f"`{HERALD_COMMAND_PREFIX} watch status`\n"
        "Show watcher outbox counts.\n\n"
        f"`{HERALD_COMMAND_PREFIX} discover`\n"
        "Fetch all enabled providers, then hold new items for review (live Twitch alerts remain time-sensitive).\n\n"
        f"`{HERALD_COMMAND_PREFIX} run once`\n"
        "Run one normal watcher cycle. New discoveries become pending and pending items auto-post if enabled.\n\n"
        f"`{HERALD_COMMAND_PREFIX} deliver pending`\n"
        "Post pending items now.\n\n"
        f"`{HERALD_COMMAND_PREFIX} held`\n"
        "Show held items.\n\n"
        f"`{HERALD_COMMAND_PREFIX} pending`\n"
        "Show pending items.\n\n"
        f"`{HERALD_COMMAND_PREFIX} posted`\n"
        "Show recently posted items.\n\n"
        f"`{HERALD_COMMAND_PREFIX} failed`\n"
        "Show failed items.\n\n"
        f"`{HERALD_COMMAND_PREFIX} post <id>`\n"
        "Post an item to its configured Discord channel and mark it posted only after success.\n\n"
        f"`{HERALD_COMMAND_PREFIX} post held <number>`\n"
        "Post the first N held items. Safety cap: 20.\n\n"
        f"`{HERALD_COMMAND_PREFIX} post held all`\n"
        "Post up to 20 held items at once.\n\n"
        f"`{HERALD_COMMAND_PREFIX} promote <id>`\n"
        "Move an item to pending.\n\n"
        f"`{HERALD_COMMAND_PREFIX} skip <id>`\n"
        "Skip one item.\n\n"
        f"`{HERALD_COMMAND_PREFIX} skip <id> <id> <id>`\n"
        "Skip multiple items by ID.\n\n"
        f"`{HERALD_COMMAND_PREFIX} skip range <start-id> <end-id>`\n"
        "Skip every item ID in a range. Maximum 200 at once.\n\n"
        f"`{HERALD_COMMAND_PREFIX} skip held <number>`\n"
        "Skip the first N currently held items.\n\n"
        f"`{HERALD_COMMAND_PREFIX} skip held all`\n"
        "Skip all held items, up to the safety limit.\n\n"
        f"`{HERALD_COMMAND_PREFIX} retry failed`\n"
        "Manually move all failed items back to pending, including items at the automatic retry cap.\n\n"
        f"`{HERALD_COMMAND_PREFIX} audit verify`\n"
        "Verify the Herald append-only audit hash chain.\n\n"
        f"`{HERALD_COMMAND_PREFIX} audit recent [number]`\n"
        "Show recent audit events in readable form. Default: 10. Maximum: 50.\n\n"
        f"`{HERALD_COMMAND_PREFIX} audit summary`\n"
        "Show audit totals by event type, category, and actor.\n\n"
        f"`{HERALD_COMMAND_PREFIX} audit item <id>`\n"
        "Show the audit trail for one Herald item.\n\n"
        f"`{HERALD_COMMAND_PREFIX} audit test`\n"
        "Create a harmless manual audit test event.\n\n"
        f"`{HERALD_COMMAND_PREFIX} clean [number]`\n"
        "Delete recent Herald messages from this DM. Default: 100. Maximum: 250.\n\n"
        f"`{HERALD_COMMAND_PREFIX} help`\n"
        "Show this help."
    )

    await send_long(message.channel, reply)


def parse_id(parts: list[str]) -> int | None:
    if not parts:
        return None

    try:
        return int(parts[0])
    except Exception:
        return None


def parse_many_ids(parts: list[str]) -> list[int]:
    ids = []

    for part in parts:
        text = str(part).strip()

        if not text.isdecimal():
            continue

        item_id = int(text)

        if item_id > 0:
            ids.append(item_id)

    return ids


async def handle_skip_command(message: discord.Message, parts: list[str]):
    if len(parts) < 2:
        await send_long(
            message.channel,
            (
                "Use one of:\n"
                "`herald skip <id>`\n"
                "`herald skip <id> <id> <id>`\n"
                "`herald skip range <start-id> <end-id>`\n"
                "`herald skip held <number>`\n"
                "`herald skip held all`"
            ),
        )
        return

    mode = parts[1].lower()

    if mode == "range":
        if len(parts) < 4:
            await send_long(message.channel, "Use: `herald skip range <start-id> <end-id>`")
            return

        try:
            start_id = int(parts[2])
            end_id = int(parts[3])
        except Exception:
            await send_long(message.channel, "Range IDs must be numbers.")
            return

        stats = skip_range(start_id, end_id)
        await send_long(message.channel, format_skip_stats(stats))
        return

    if mode == "held":
        if len(parts) < 3:
            await send_long(message.channel, "Use: `herald skip held <number>` or `herald skip held all`")
            return

        amount = parts[2].lower()

        if amount == "all":
            stats = skip_held_items(None)
            await send_long(message.channel, format_skip_stats(stats))
            return

        try:
            limit = int(amount)
        except Exception:
            await send_long(message.channel, "Use a number, or `all`.")
            return

        stats = skip_held_items(limit)
        await send_long(message.channel, format_skip_stats(stats))
        return

    item_ids = parse_many_ids(parts[1:])

    if not item_ids:
        await send_long(message.channel, "Use item IDs, for example: `herald skip 12 13 14`")
        return

    stats = skip_items(item_ids)
    await send_long(message.channel, format_skip_stats(stats))


async def handle_owner_command(message: discord.Message, body: str):
    body_clean = (body or "").strip()
    body_lower = body_clean.lower()
    parts = body_clean.split()

    if body_lower in {"", "help", "commands"}:
        await handle_help(message)
        return

    if parts and parts[0].lower() in {"clean", "cleanup", "clear"}:
        limit = DM_CLEAN_DEFAULT_LIMIT

        if len(parts) >= 2:
            try:
                limit = int(parts[1])
            except Exception:
                await send_long(
                    message.channel,
                    f"Use: `{HERALD_COMMAND_PREFIX} clean 100`",
                )
                return

        await clean_dm_messages(message, limit)
        return    

    if body_lower in {"status", "stat"}:
        await handle_status(message)
        return

    if body_lower in {"welcome test", "test welcome"}:
        guild = configured_guild()

        if guild is None:
            await send_long(message.channel, f"⚠️ {configured_guild_error()}")
            return

        member = guild.get_member(message.author.id) or guild.me

        if member is None:
            await send_long(
                message.channel,
                "⚠️ I could not find a server member to use for the welcome test.",
            )
            return

        ok, detail = await post_welcome_for_member(
            member,
            event_type="manual_welcome_test",
        )

        if ok:
            await send_long(message.channel, f"{emoji('herald')} {detail}")
        else:
            await send_long(message.channel, f"⚠️ {detail}")

        return

    if body_lower in {"runtime", "watch runtime", "watcher runtime"}:
        await send_long(message.channel, runtime_status_text())
        return

    if body_lower in {
        "subs panel",
        "subscriptions panel",
        "post subscriptions",
    }:
        guild = configured_guild()

        if guild is None:
            await send_long(message.channel, f"⚠️ {configured_guild_error()}")
            return

        target = find_text_channel_by_name(guild, SUBSCRIPTIONS_CHANNEL_NAME)

        if target is None:
            await send_long(
                message.channel,
                f"⚠️ I could not find the configured subscriptions channel `#{SUBSCRIPTIONS_CHANNEL_NAME}`.",
            )
            return

        await post_subscription_panel(target)
        await send_long(
            message.channel,
            f"{emoji('herald')} Posted the Herald subscription panel in {target.mention}.",
        )
        return

    if body_lower in {"watch status", "watcher status", "rss status"}:
        await send_long(message.channel, watcher_status_text())
        return

    if body_lower in {"discover", "watch discover", "rss discover"}:
        await message.channel.send(f"{emoji('herald')} Discovering Herald watcher items into held...")
        try:
            stats = await asyncio.to_thread(discover_items, "held")
            await send_long(message.channel, format_discover_stats(stats))
        except Exception as e:
            await send_long(message.channel, f"Discovery failed: `{e}`")
        return

    if body_lower in {"run once", "watch run once", "watcher run once"}:
        await message.channel.send(f"{emoji('herald')} Running one Herald watcher cycle...")
        try:
            result = await run_watcher_cycle(first_run=False)
            discovery = result.get("discovery", {})
            delivery = result.get("delivery", {})
            await send_long(
                message.channel,
                (
                    f"{emoji('herald')} **Watcher cycle complete**\n\n"
                    f"Queue status for new items: `{result.get('queue_status')}`\n"
                    f"Seen: `{discovery.get('seen', 0)}`\n"
                    f"Created: `{discovery.get('created', 0)}`\n"
                    f"Existing: `{discovery.get('existing', 0)}`\n"
                    f"Failed items requeued: `{delivery.get('requeued', 0)}`\n"
                    f"Delivery checked: `{delivery.get('checked', 0)}`\n"
                    f"Posted: `{delivery.get('posted', 0)}`\n"
                    f"Failed: `{delivery.get('failed', 0)}`"
                ),
            )
        except Exception as e:
            await send_long(message.channel, f"Watcher cycle failed: `{e}`")
        return

    if body_lower in {"deliver pending", "post pending", "post watcher pending"}:
        await message.channel.send(f"{emoji('herald')} Delivering pending Herald items...")
        delivery = await deliver_pending_items_once(HERALD_POST_BATCH_LIMIT)
        await send_long(
            message.channel,
            (
                f"Delivery checked: `{delivery.get('checked', 0)}`\n"
                f"Posted: `{delivery.get('posted', 0)}`\n"
                f"Failed: `{delivery.get('failed', 0)}`\n"
                f"Missing: `{delivery.get('missing', 0)}`"
            ),
        )
        return

    if parts and len(parts) >= 3 and parts[0].lower() == "post" and parts[1].lower() == "held":
        amount_text = parts[2].lower()

        if amount_text == "all":
            limit = 20
        else:
            try:
                limit = int(amount_text)
            except Exception:
                await send_long(
                    message.channel,
                    "Use: `herald post held <number>` or `herald post held all`",
                )
                return

        await message.channel.send(
            f"{emoji('herald')} Posting up to `{limit}` held Herald item(s)..."
        )

        delivery = await post_held_items_once(limit)

        if delivery.get("error"):
            await send_long(message.channel, f"Could not post held items: `{delivery['error']}`")
            return

        await send_long(
            message.channel,
            (
                f"{emoji('herald')} **Held batch post complete**\n\n"
                f"Checked: `{delivery.get('checked', 0)}`\n"
                f"Posted: `{delivery.get('posted', 0)}`\n"
                f"Failed: `{delivery.get('failed', 0)}`\n"
                f"Missing: `{delivery.get('missing', 0)}`"
            ),
        )
        return

    if body_lower in {"held", "watch held", "rss held"}:
        await send_long(message.channel, held_items_text(10))
        return

    if body_lower in {"pending", "watch pending", "rss pending"}:
        await send_long(message.channel, pending_items_text(10))
        return

    if body_lower in {"posted", "watch posted", "rss posted"}:
        await send_long(message.channel, posted_items_text(10))
        return

    if body_lower in {"failed", "watch failed", "rss failed"}:
        await send_long(message.channel, failed_items_text(10))
        return

    if body_lower in {"retry failed", "watch retry failed", "rss retry failed"}:
        changed = retry_failed()
        await send_long(
            message.channel,
            f"Moved `{changed}` failed Herald item(s) back to pending.",
        )
        return

    if body_lower in {"audit verify", "verify audit", "audit chain"}:
        await send_long(message.channel, audit_verify_text())
        return

    if parts and len(parts) >= 2 and parts[0].lower() == "audit":
        mode = parts[1].lower()

        if mode in {"verify", "chain"}:
            await send_long(message.channel, audit_verify_text())
            return

        if mode in {"recent", "last", "latest"}:
            limit = 10

            if len(parts) >= 3:
                try:
                    limit = int(parts[2])
                except Exception:
                    await send_long(
                        message.channel,
                        f"Use: `{HERALD_COMMAND_PREFIX} audit recent 10`",
                    )
                    return

            await send_long(message.channel, audit_recent_text(limit))
            return

        if mode in {"summary", "stats"}:
            await send_long(message.channel, audit_summary_text())
            return

        if mode == "item":
            item_id = parse_id(parts[2:])

            if item_id is None:
                await send_long(message.channel, "Use: `herald audit item <id>`")
                return

            await send_long(message.channel, audit_item_text(item_id))
            return

        if mode == "test":
            audit_event(
                event_type="manual_test",
                item_id=None,
                actor="owner",
                old_status="",
                new_status="",
                reason="owner_test",
                detail=f"Manual audit test requested by {message.author}",
                payload={
                    "command": "audit test",
                    "user_id": str(message.author.id),
                    "channel": "dm" if isinstance(message.channel, discord.DMChannel) else "server",
                },
            )

            await send_long(
                message.channel,
                (
                    f"{emoji('herald')} **Herald audit test event created**\n\n"
                    f"Audit events logged: `{count_audit_events()}`\n\n"
                    f"{audit_verify_text()}"
                ),
            )
            return

        await send_long(
            message.channel,
            "Use one of:\n"
            "`herald audit verify`\n"
            "`herald audit recent [number]`\n"
            "`herald audit summary`\n"
            "`herald audit item <id>`\n"
            "`herald audit test`",
        )
        return

    if parts and parts[0].lower() == "skip":
        await handle_skip_command(message, parts)
        return

    if parts and parts[0].lower() in {"post"}:
        item_id = parse_id(parts[1:])

        if item_id is None:
            await send_long(message.channel, "Use: `herald post <id>`")
            return

        item = get_item(item_id)

        if not item:
            await send_long(message.channel, f"I could not find Herald item `{item_id}`.")
            return

        await send_long(
            message.channel,
            f"{emoji('herald')} Posting Herald item `{item_id}` to its target channel...",
        )

        ok, detail = await post_item_to_discord(item)

        if ok:
            mark_posted(item_id, detail)
            await send_long(
                message.channel,
                f"Posted Herald item `{item_id}` successfully. Discord message ID: `{detail}`",
            )
        else:
            mark_failed(item_id, detail)
            await send_long(
                message.channel,
                f"Failed to post Herald item `{item_id}`: `{detail}`",
            )

        return

    if parts and parts[0].lower() in {"promote", "queue"}:
        item_id = parse_id(parts[1:])

        if item_id is None:
            await send_long(message.channel, "Use: `herald promote <id>`")
            return

        if promote_item(item_id):
            await send_long(message.channel, f"Moved Herald item `{item_id}` to pending.")
        else:
            await send_long(message.channel, f"I could not find Herald item `{item_id}`.")
        return

    await send_long(
        message.channel,
        f"Unknown Herald command. Use `{HERALD_COMMAND_PREFIX} help`.",
    )


@client.event
async def on_ready():
    global persistent_view_registered, watcher_loop_started

    init_db()

    if not persistent_view_registered:
        client.add_view(SubscriptionView(register_all=True))
        persistent_view_registered = True

    print(
        f"{HERALD_NAME} logged in as {client.user} "
        f"(guilds={len(client.guilds)})",
        flush=True,
    )

    log_event(
        "startup",
        detail=f"Logged in as {client.user}; guilds={len(client.guilds)}",
    )

    if not watcher_loop_started:
        watcher_loop_started = True
        asyncio.create_task(run_watchers_loop())
        print("Herald watcher loop task created.", flush=True)


@client.event
async def on_member_join(member: discord.Member):
    guild = configured_guild()

    if guild is None or member.guild.id != guild.id:
        return

    ok, detail = await post_welcome_for_member(
        member,
        event_type="member_join_welcome",
    )

    if not ok:
        print(detail, flush=True)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)

    if is_dm:
        if not HERALD_DM_COMMANDS_ENABLED:
            return

        if not is_owner(message.author):
            log_event(
                "non_owner_dm_ignored",
                user_id=str(message.author.id),
                detail=f"DM ignored from {message.author}",
            )

            if HERALD_REPLY_TO_NON_OWNER_DMS:
                await message.channel.send(
                    "Herald Angel is an announcement bot and does not accept DMs."
                )

            return

        prompt = clean_prompt(message)
        body = command_body(prompt)

        if body is None:
            await message.channel.send(
                f"Use `{HERALD_COMMAND_PREFIX} help` for Herald commands."
            )
            return

        await handle_owner_command(message, body)
        return

    if not HERALD_SERVER_COMMANDS_ENABLED:
        return

    if HERALD_OWNER_ONLY and not is_owner(message.author):
        return

    mentioned = client.user in message.mentions if client.user else False
    prompt = clean_prompt(message)
    body = command_body(prompt)

    if not mentioned and body is None:
        return

    if body is None:
        body = prompt

    await handle_owner_command(message, body)


def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Check the project .env file")

    if OWNER_ID <= 0:
        raise RuntimeError("OWNER_ID is missing or invalid. Check the project .env file")

    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
