import asyncio
import re
import time
import sys
import config
import storage
from instance_lock import acquire_instance
from urllib.parse import urlsplit
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import discord
from presentation import format_published_at, escape_provider_text
from provider_runtime import get_configured_sources
from delivery import DeliveryCoordinator
from storage import recover_interrupted_claims

from subscriptions import (
    SubscriptionView,
    configure_subscriptions,
    subscription_views,
    get_subscription_definitions,
    validate_subscription_role,
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
    FREE_GAMES_ENABLED,
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
    discover_items_async,
    failed_items_text,
    format_discover_stats,
    format_skip_stats,
    get_item,
    held_items,
    held_items_text,
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

class HeraldClient(discord.Client):
    async def setup_hook(self):
        if getattr(self, "_herald_setup_complete", False):
            return
        from slash_commands import synchronize_commands
        self._instance_lease = acquire_instance(storage._db_path())
        self._herald_tasks = []
        try:
            init_db()
            recover_interrupted_claims()
            configure_subscriptions(HERALD_GUILD_ID, get_configured_sources)
            # Register stable IDs for future feed additions, even empty pages.
            for page in range(11):
                self.add_view(SubscriptionView(page=page, register_empty=True))
            self.add_view(SubscriptionView(register_all=True))
            await synchronize_commands(self, sys.modules[__name__])
            self._herald_setup_complete = True
        except BaseException:
            self._instance_lease.close()
            self._instance_lease = None
            raise

    async def close(self):
        tasks = getattr(self, "_herald_tasks", [])
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await super().close()
        finally:
            lease = getattr(self, "_instance_lease", None)
            if lease is not None:
                lease.close()
                self._instance_lease = None



client = HeraldClient(intents=intents)

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
    matches = [channel for channel in guild.text_channels if channel.name.lower() == (name or "").lower()]
    return matches[0] if len(matches) == 1 else None


def configured_guild() -> discord.Guild | None:
    return client.get_guild(HERALD_GUILD_ID) if HERALD_GUILD_ID > 0 else None


def configured_guild_error() -> str:
    return "Set HERALD_GUILD_ID to the intended server ID and ensure Herald belongs to it."


async def post_subscription_panel(target_channel: discord.TextChannel) -> None:
    guild = configured_guild()
    if guild is None or target_channel.guild.id != guild.id:
        raise ValueError("wrong_target_guild")
    for page, view in enumerate(subscription_views()):
        await target_channel.send(embed=make_subscription_embed(guild, page=page),
                                  view=view, allowed_mentions=discord.AllowedMentions.none())


def build_welcome_message(member: discord.Member) -> str:
    return (
        f"{emoji('herald')} Welcome to **{sanitize_discord_text(member.guild.name, 100)}**, {member.mention}! "
        "Make yourself comfy."
    )


async def post_welcome_for_member(
    member: discord.Member,
    event_type: str = "member_join_welcome",
) -> tuple[bool, str]:
    if not WELCOME_ENABLED:
        return False, "Welcome messages are disabled."

    guild = configured_guild()
    if guild is None or member.guild.id != guild.id:
        return False, "Welcome target does not match the configured server."
    channel = (guild.get_channel(config.WELCOME_CHANNEL_ID) if config.WELCOME_CHANNEL_ID
               else find_text_channel_by_name(guild, WELCOME_CHANNEL_NAME))

    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
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
        return False, "Welcome failed; check configured channel and Discord permissions."


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
    return escape_provider_text(value, max_chars)


def safe_http_url(value: str, max_chars: int = 1000) -> str:
    url = str(value or "").strip()
    if len(url) > max_chars or any(ord(ch) <= 32 for ch in url) or any(ch in url for ch in "<>\\"):
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        parsed.port
    except ValueError:
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


def build_free_game_embed(item: dict) -> discord.Embed:
    return build_post_payload_for_item(item, configured_guild())[1]


def source_policy(item: dict) -> dict:
    source_id = item.get("source_id") or item.get("source")
    policies = [s for s in get_configured_sources() if s.get("id", s.get("source_id")) == source_id]
    if len(policies) != 1 or not policies[0].get("enabled"):
        raise ValueError("source_disabled_or_missing")
    policy = policies[0]
    if (item.get("provider_id") != policy.get("provider_id")
            or not item.get("source_policy_digest")
            or item["source_policy_digest"] != policy.get("policy_digest")):
        raise ValueError("source_policy_changed_rediscover_required")
    pairs = (("destination_channel_id", "channel_id"), ("subscription_role_id", "role_id"))
    if any(int(item.get(a) or 0) != int(policy.get(b) or 0) for a, b in pairs):
        raise ValueError("source_policy_changed_rediscover_required")
    if item.get("delivery_mode") != policy.get("delivery_mode"):
        raise ValueError("source_policy_changed_rediscover_required")
    return policy


def allowed_mentions_for_item(item: dict, guild: discord.Guild) -> discord.AllowedMentions:
    role_id = int(item.get("subscription_role_id") or 0)
    if role_id:
        definitions = [d for d in get_subscription_definitions() if d.role_id == role_id
                       and d.source_id == (item.get("source_id") or item.get("source"))]
        if len(definitions) != 1:
            raise ValueError("notification_role_not_configured")
        role, error = validate_subscription_role(guild, definitions[0])
        if error or role is None:
            raise ValueError("notification_role_not_safe")
        return discord.AllowedMentions(everyone=False, users=False, roles=[role], replied_user=False)
    return discord.AllowedMentions.none()


def build_post_payload_for_item(item: dict, guild: discord.Guild) -> tuple[str, discord.Embed]:
    cleaned = sanitized_item_for_post(item)
    url = cleaned.get("url")
    if not url:
        raise ValueError("canonical_url_invalid")
    embed = discord.Embed(title=cleaned.get("title") or "Herald update", url=url,
                          description=cleaned.get("summary") or None)
    embed.add_field(name="Source", value=cleaned.get("source") or "Source", inline=True)
    if cleaned.get("tags"):
        embed.add_field(name="Tags", value=cleaned["tags"], inline=True)
    published = format_published_at(str(item.get("published_at") or "")[:100])
    if published:
        embed.add_field(name="Published", value=sanitize_discord_text(published, 100), inline=True)
    image_url = cleaned.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    attribution = safe_http_url(item.get("attribution_url") or "")
    source_id = str(item.get("source_id") or item.get("source") or "").lower()
    if source_id == "gamerpower" or source_id.startswith("gamerpower:"):
        attribution = "https://www.gamerpower.com/"
    if attribution:
        label = sanitize_discord_text(item.get("attribution_label") or "Attribution", 100)
        embed.add_field(name=label, value=f"<{attribution}>", inline=False)
    # Canonical and attribution URLs stay visible and separate from escaped text.
    role_id = int(item.get("subscription_role_id") or 0)
    prefix = f"<@&{role_id}>\n" if role_id else ""
    content = f"{prefix}{emoji('herald')} **Herald update**\n<{url}>"
    return content, embed


def prepare_delivery(item: dict):
    guild = configured_guild()
    if guild is None:
        raise ValueError("target_guild_unavailable")
    policy = source_policy(item)
    channel = guild.get_channel(int(policy["channel_id"]))
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise ValueError("destination_unavailable")
    perms = channel.permissions_for(guild.me)
    if not all((perms.view_channel, perms.send_messages, perms.embed_links)):
        raise ValueError("destination_permissions_missing")
    content, embed = build_post_payload_for_item(item, guild)
    return channel, content, embed, allowed_mentions_for_item(item, guild)


async def send_delivery(item: dict, payload):
    channel, content, embed, mentions = payload
    sent = await channel.send(content=content[:1900], embed=embed, allowed_mentions=mentions)
    return str(sent.id)


delivery_coordinator = DeliveryCoordinator(prepare_delivery, send_delivery)


async def post_item_to_discord(item: dict) -> tuple[bool, str]:
    result = await delivery_coordinator.deliver_one(
        int(item["id"]), approve=True, actor="owner", expected_revision=item.get("revision")
    )
    detail = result.get("message_id") or (
        f"{result.get('current_status', result['status'])}: {result.get('error', '')}"
    )
    return result["status"] == "posted", str(detail)


async def deliver_pending_items_once(limit: int = HERALD_POST_BATCH_LIMIT) -> dict:
    stats = await delivery_coordinator.deliver_batch(limit=limit, actor="automatic")
    WATCHER_RUNTIME["last_delivery"] = stats
    return stats


async def post_held_items_once(limit: int = 5, max_limit: int = 20) -> dict:
    if not 1 <= int(limit) <= max_limit:
        return {"error": f"Limit must be between 1 and {max_limit}."}
    return await delivery_coordinator.deliver_batch(
        limit=int(limit), approve=True, status="held", actor="owner"
    )


async def run_watcher_cycle(first_run: bool = False, deliver: bool = False) -> dict:
    queue_status = startup_mode_to_status(HERALD_STARTUP_BACKLOG_MODE) if first_run else "pending"
    WATCHER_RUNTIME["last_poll"] = now_text()
    discovery = await discover_items_async(queue_status)
    WATCHER_RUNTIME["last_discovery"] = discovery
    WATCHER_RUNTIME["last_provider_errors"] = list(discovery.get("errors") or [])
    WATCHER_RUNTIME["provider_health"] = list(discovery.get("health") or [])
    if not discovery.get("errors") and not discovery.get("busy"):
        WATCHER_RUNTIME["last_success"] = now_text()
    WATCHER_RUNTIME["last_error"] = "provider_discovery_degraded" if discovery.get("errors") else ""
    delivery = await deliver_pending_items_once(HERALD_POST_BATCH_LIMIT) if deliver else {}
    return {"discovery": discovery, "delivery": delivery, "queue_status": queue_status}


async def run_watchers_loop():
    first_run = True
    WATCHER_RUNTIME["running"] = True
    try:
        while not client.is_closed():
            try:
                result = await run_watcher_cycle(first_run=first_run)
                if not result["discovery"].get("busy"):
                    first_run = False
                    WATCHER_RUNTIME["first_run_complete"] = True
            except Exception:
                WATCHER_RUNTIME["last_error"] = "discovery_cycle_failed"
            await asyncio.sleep(HERALD_CHECK_SECONDS)
    finally:
        WATCHER_RUNTIME["running"] = False


async def run_delivery_loop():
    # Separate task: no discovery await, executor pool or shared worker dependency.
    while not client.is_closed():
        if HERALD_AUTO_POST_ENABLED:
            try:
                retry_failed(HERALD_DELIVERY_MAX_ATTEMPTS)
                await deliver_pending_items_once(HERALD_POST_BATCH_LIMIT)
                WATCHER_RUNTIME["last_delivery_error"] = ""
            except Exception:
                WATCHER_RUNTIME["last_delivery_error"] = "delivery_cycle_failed"
        await asyncio.sleep(config.HERALD_DELIVERY_SECONDS)


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
        f"Last delivery failed: `{delivery.get('failed', 0)}`; uncertain: `{delivery.get('uncertain', 0)}`"
    )


async def handle_status(message: discord.Message):
    from diagnostics import status_text
    await send_long(message.channel, status_text(sys.modules[__name__]))


async def handle_help(message: discord.Message):
    await send_long(message.channel,
        "**Herald commands**\nUse `/herald` for the normal interactive interface.\n\n"
        "Owner DM recovery forms:\n"
        "`herald status` · `herald doctor` · `herald about`\n"
        "`herald discover` · `herald run once` · `herald deliver pending`\n"
        "`herald held` · `herald pending` · `herald posted` · `herald failed` · `herald uncertain`\n"
        "`herald post <id>` · `herald promote <id>` · `herald skip <id>`\n"
        "`herald skip range <start> <end>` · `herald retry failed`\n"
        "`herald resolve <id> posted <message_id>` · `herald resolve <id> retry|skip`\n"
        "`herald subs panel` · `herald welcome test` · `herald clean <number>`\n"
        "`herald audit verify` · `herald audit recent <number>` · `herald audit summary` · `herald audit item <id>`")


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
    # Enforce at the shared handler as well as the transport boundary.
    if not is_owner(message.author):
        return
    if message.guild is not None and message.guild.id != HERALD_GUILD_ID:
        return
    body_clean = (body or "").strip()
    body_lower = body_clean.lower()
    parts = body_clean.split()
    if body_lower in {"doctor", "about"}:
        import diagnostics
        fn = diagnostics.doctor_text if body_lower == "doctor" else diagnostics.about_text
        await send_long(message.channel, fn(sys.modules[__name__]))
        return
    if body_lower == "uncertain":
        from storage import list_items_by_status
        from watchers import format_items
        await send_long(message.channel, format_items("Uncertain — reconcile before retry", list_items_by_status("uncertain", 20)))
        return
    if parts and parts[0].lower() == "resolve":
        from storage import resolve_uncertain
        try:
            item_id = int(parts[1])
            resolution = parts[2].lower()
            message_id = parts[3] if len(parts) > 3 else ""
            changed = resolve_uncertain(item_id, resolution, message_id=message_id)
            await send_long(message.channel, "Resolution recorded." if changed else "Item changed or resolution invalid; inspect its current state.")
        except (ValueError, IndexError):
            await send_long(message.channel, "Use: herald resolve <id> posted <message_id>, or herald resolve <id> retry|skip. Retry acknowledges possible duplicate delivery.")
        return

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

        target = guild.get_channel(config.SUBSCRIPTIONS_CHANNEL_ID) if config.SUBSCRIPTIONS_CHANNEL_ID else find_text_channel_by_name(guild, SUBSCRIPTIONS_CHANNEL_NAME)

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
            stats = await discover_items_async("held")
            await send_long(message.channel, format_discover_stats(stats))
        except Exception as e:
            await send_long(message.channel, "Discovery failed; check herald doctor.")
        return

    if body_lower in {"run once", "watch run once", "watcher run once"}:
        await message.channel.send(f"{emoji('herald')} Running one Herald watcher cycle...")
        try:
            result = await run_watcher_cycle(first_run=False, deliver=True)
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
                    f"Failed: `{delivery.get('failed', 0)}`; uncertain: `{delivery.get('uncertain', 0)}`; blocked: `{delivery.get('blocked', 0)}`"
                ),
            )
        except Exception as e:
            await send_long(message.channel, "Watcher cycle failed; check herald doctor.")
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
                f"Uncertain: `{delivery.get('uncertain', 0)}`; blocked: `{delivery.get('blocked', 0)}`\n"
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
                f"Uncertain: `{delivery.get('uncertain', 0)}`; blocked: `{delivery.get('blocked', 0)}`\n"
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
            await send_long(
                message.channel,
                f"Posted Herald item `{item_id}` successfully. Discord message ID: `{detail}`",
            )
        else:
            await send_long(
                message.channel,
                f"Item `{item_id}` was not confirmed posted: `{detail}`. Inspect its current state before retrying.",
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
            await send_long(message.channel, f"Item `{item_id}` is missing, in flight, or cannot transition; inspect current status.")
        return

    await send_long(
        message.channel,
        f"Unknown Herald command. Use `{HERALD_COMMAND_PREFIX} help`.",
    )


@client.event
async def on_ready():
    global watcher_loop_started

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
        client._herald_tasks = [asyncio.create_task(run_watchers_loop()),
                                asyncio.create_task(run_delivery_loop())]
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
                    "Herald is an announcement bot and does not accept DMs."
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

    if not is_owner(message.author) or message.guild is None or message.guild.id != HERALD_GUILD_ID:
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

    if HERALD_GUILD_ID <= 0:
        raise RuntimeError("HERALD_GUILD_ID must identify the target server")

    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
