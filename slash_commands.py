"""One owner-only, target-guild-only application command tree for Herald.

Call synchronize_commands from Client.setup_hook, never on_ready. Commands and
review components share the storage/coordinator API used by owner DMs.
"""

import asyncio
from typing import Literal
from urllib.parse import urlsplit

import discord
from discord import app_commands

import config
import diagnostics
import feed_config
import provider_runtime
import storage
from presentation import escape_provider_text


def authorized(interaction, services=None):
    owner = getattr(config, "OWNER_ID", 0)
    target = getattr(config, "HERALD_GUILD_ID", 0)
    return (isinstance(owner, int) and owner > 0 and isinstance(target, int) and target > 0
            and getattr(getattr(interaction, "user", None), "id", None) == owner
            and getattr(interaction, "guild_id", None) == target
            and getattr(getattr(interaction, "guild", None), "id", None) == target)


async def respond(interaction, text, **kwargs):
    text = str(text or "Done.")
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    for index, chunk in enumerate(chunks):
        options = dict(ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        if index == 0:
            options.update(kwargs)
        sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await sender(chunk, **options)


async def begin(interaction, services=None):
    if not authorized(interaction, services):
        await respond(interaction, "Only the configured owner may use Herald commands in the configured target server.")
        return False
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    return True


def display_text(value, limit=500):
    return discord.utils.escape_mentions(escape_provider_text(str(value or ""), limit=limit))


def source_url(value):
    value = str(value or "")
    try:
        parsed = urlsplit(value)
        if (len(value) <= 1000 and parsed.scheme in ("http", "https") and parsed.hostname
                and not parsed.username and not parsed.password
                and not any(ord(char) < 33 or char in "<>\\" for char in value)):
            return value
    except ValueError:
        pass
    return None


def item_text(item, details=False):
    if not item:
        return "Queue item not found."
    text = (f"Item {int(item['id'])} · {display_text(item.get('status'), 20)} · "
            f"revision {int(item.get('revision', 1))}\n{display_text(item.get('title'), 200)}")
    if details:
        text += "\n" + display_text(item.get("summary"), 1000)
        text += "\nSource: " + display_text(item.get("source"), 80)
        text += "\nMode: " + display_text(item.get("delivery_mode"), 20)
    if item.get("status") == "uncertain":
        text += "\nDiscord may already have accepted this item. Check the channel, then use /herald queue resolve deliberately."
    return text


def delivery_result_text(result):
    status = result.get("current_status") if result.get("status") == "blocked" else result.get("status")
    if status == "posted":
        return "Posted; the queue receipt is recorded."
    if status == "uncertain":
        return "Delivery is uncertain. Check Discord before using /herald queue resolve; automatic reposting is blocked."
    if status == "sending":
        return "The item is already in flight. Its state has not been changed."
    return "The item was not posted. Reload it to check its revision, eligibility and current state."


class QueueReviewView(discord.ui.View):
    def __init__(self, services, item):
        super().__init__(timeout=600)
        self.services = services
        self.item_id = int(item["id"])
        self.expected_revision = int(item.get("revision", 1))
        url = source_url(item.get("url"))
        if url:
            self.add_item(discord.ui.Button(label="Open Source", url=url))

    async def interaction_check(self, interaction):
        if authorized(interaction, self.services):
            return True
        await respond(interaction, "This review is only available to the configured owner in the target server.")
        return False

    async def on_error(self, interaction, error, item):
        await respond(interaction, "Review action failed safely. Reload the item; no raw diagnostic details are shown.")

    async def current(self, interaction):
        if not await begin(interaction, self.services):
            return None
        item = storage.get_item_by_id(self.item_id)
        if item is None or int(item.get("revision", 1)) != self.expected_revision:
            await respond(interaction, "This review is stale. Open /herald queue review again to inspect the current revision.")
            return None
        if item.get("status") in ("sending", "uncertain", "posted", "skipped"):
            await respond(interaction, item_text(item, details=True) + "\nThis review cannot change that state.")
            return None
        return item

    @discord.ui.button(label="Post", style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.current(interaction)
        if item:
            result = await self.services.delivery_coordinator.deliver_one(
                self.item_id, approve=True, actor="owner", expected_revision=self.expected_revision)
            await respond(interaction, delivery_result_text(result))

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.current(interaction)
        if item:
            changed = storage.set_item_status(self.item_id, "skipped", actor="owner", reason="review_skip",
                                              expected_revision=self.expected_revision)
            await respond(interaction, "Skipped." if changed else "Item changed or is in flight; reload its state.")

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await begin(interaction, self.services):
            return
        await show_review(interaction, self.services, after_id=self.item_id)

    @discord.ui.button(label="Details", style=discord.ButtonStyle.secondary)
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = await self.current(interaction)
        if item:
            await respond(interaction, item_text(item, details=True))


async def show_review(interaction, services, item_id=None, after_id=None):
    if item_id:
        item = storage.get_item_by_id(item_id)
    else:
        items = storage.list_items_by_status("held", limit=200, newest_first=False)
        later = [row for row in items if after_id is None or int(row["id"]) > after_id]
        item = (later or [row for row in items if int(row["id"]) != after_id] or [None])[0]
    if not item:
        await respond(interaction, "No held review item is available.")
        return
    await respond(interaction, item_text(item, details=True), view=QueueReviewView(services, item))


class HeraldCommandTree(app_commands.CommandTree):
    def __init__(self, client, services):
        super().__init__(client)
        self.services = services

    async def interaction_check(self, interaction):
        if authorized(interaction, self.services):
            return True
        await respond(interaction, "Only the configured owner may use Herald commands in the target server.")
        return False

    async def on_error(self, interaction, error):
        await respond(interaction, "Herald could not complete that operation. Run /herald doctor; raw error details are withheld.")


def install_commands(client, services):
    """Construct/register once locally. No Discord request is performed here."""
    existing = getattr(client, "_herald_tree", None)
    if existing is not None:
        return existing
    if getattr(config, "HERALD_COMMAND_SCOPE", "guild") not in ("guild", "global"):
        raise ValueError("Invalid command registration scope")
    if getattr(config, "HERALD_GUILD_ID", 0) <= 0 or getattr(config, "OWNER_ID", 0) <= 0:
        raise ValueError("Explicit target guild and owner IDs are required")
    tree = HeraldCommandTree(client, services)
    herald = app_commands.Group(name="herald", description="Herald owner controls", guild_only=True)
    queue = app_commands.Group(name="queue", description="Inspect and deliver the durable queue", parent=herald)
    feed = app_commands.Group(name="feed", description="Manage generic RSS and Atom feeds", parent=herald)
    setup = app_commands.Group(name="setup", description="Configure Discord panels", parent=herald)
    audit = app_commands.Group(name="audit", description="Inspect the append-only audit chain", parent=herald)

    @herald.command(name="status", description="Show sources, delivery, queue and runtime status")
    async def status(interaction: discord.Interaction):
        if await begin(interaction, services):
            await respond(interaction, diagnostics.status_text(services))

    @herald.command(name="doctor", description="Run sanitised checks safe to share in an issue")
    async def doctor(interaction: discord.Interaction):
        if await begin(interaction, services):
            await respond(interaction, diagnostics.doctor_text(services, client))

    @herald.command(name="about", description="Show Herald, database and runtime versions")
    async def about(interaction: discord.Interaction):
        if await begin(interaction, services):
            await respond(interaction, diagnostics.about_text())

    @herald.command(name="help", description="Show the preferred Herald command workflow")
    async def help_command(interaction: discord.Interaction):
        if await begin(interaction, services):
            await respond(interaction, "Use /herald status or doctor to check health; /herald run discovers items. "
                          "Use /herald queue review for Post/Skip/Next, or queue list/inspect/post/promote/retry/deliver. "
                          "Resolve uncertain deliveries only after checking Discord. "
                          "Manage RSS with /herald feed; panels with /herald setup; evidence with /herald audit. "
                          "Owner DM recovery commands remain available: herald help.")

    @herald.command(name="run", description="Run one bounded discovery and delivery cycle")
    async def run(interaction: discord.Interaction):
        if await begin(interaction, services):
            await services.run_watcher_cycle(first_run=False, deliver=True)
            await respond(interaction, diagnostics.status_text(services))

    @queue.command(name="list", description="List queue items by state")
    async def queue_list(interaction: discord.Interaction,
                         state: Literal["held", "pending", "sending", "uncertain", "failed", "posted", "skipped"] = "held",
                         limit: app_commands.Range[int, 1, 20] = 10):
        if await begin(interaction, services):
            items = storage.list_items_by_status(state, limit=limit)
            await respond(interaction, "\n\n".join(item_text(item) for item in items) or "No items in that state.")

    @queue.command(name="review", description="Review a held item with revision-bound controls")
    async def queue_review(interaction: discord.Interaction, item_id: int | None = None):
        if await begin(interaction, services):
            if item_id is not None and item_id <= 0:
                await respond(interaction, "Item IDs must be positive.")
                return
            await show_review(interaction, services, item_id=item_id)

    @queue.command(name="inspect", description="Inspect one current queue record")
    async def queue_inspect(interaction: discord.Interaction, item_id: app_commands.Range[int, 1]):
        if await begin(interaction, services):
            await respond(interaction, item_text(storage.get_item_by_id(item_id), details=True))

    @queue.command(name="post", description="Approve the current revision and attempt one delivery")
    async def queue_post(interaction: discord.Interaction, item_id: app_commands.Range[int, 1]):
        if await begin(interaction, services):
            item = storage.get_item_by_id(item_id)
            if item is None:
                await respond(interaction, "Queue item not found.")
                return
            result = await services.delivery_coordinator.deliver_one(item_id, approve=True, actor="owner",
                                                                      expected_revision=item["revision"])
            await respond(interaction, delivery_result_text(result))

    @queue.command(name="skip", description="Skip one eligible item without changing in-flight content")
    async def queue_skip(interaction: discord.Interaction, item_id: app_commands.Range[int, 1]):
        if await begin(interaction, services):
            item = storage.get_item_by_id(item_id)
            changed = item and storage.set_item_status(item_id, "skipped", actor="owner", reason="slash_skip",
                                                       expected_revision=item["revision"])
            await respond(interaction, "Skipped." if changed else "Not changed: missing, changed, final or in flight. Inspect the item.")

    @queue.command(name="promote", description="Approve the current revision for later queued delivery")
    async def queue_promote(interaction: discord.Interaction, item_id: app_commands.Range[int, 1]):
        if await begin(interaction, services):
            item = storage.get_item_by_id(item_id)
            changed = item and storage.approve_item(item_id, expected_revision=item["revision"], actor="owner")
            await respond(interaction, "Current revision approved." if changed else "Not approved; inspect the current state.")

    @queue.command(name="retry", description="Approve one failed item for another delivery attempt")
    async def queue_retry(interaction: discord.Interaction, item_id: app_commands.Range[int, 1]):
        if await begin(interaction, services):
            item = storage.get_item_by_id(item_id)
            if not item or item["status"] != "failed":
                await respond(interaction, "Only failed items can use retry. Uncertain items need explicit resolve.")
                return
            changed = storage.approve_item(item_id, expected_revision=item["revision"], actor="owner")
            await respond(interaction, "Retry queued." if changed else "Not changed; inspect current state.")

    @queue.command(name="deliver", description="Attempt a bounded batch of pending items")
    async def queue_deliver(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20] = 5):
        if await begin(interaction, services):
            await services.delivery_coordinator.deliver_batch(limit=limit, actor="owner")
            await respond(interaction, diagnostics.status_text(services))

    @queue.command(name="resolve", description="Resolve uncertainty after checking for an existing Discord message")
    async def queue_resolve(interaction: discord.Interaction, item_id: app_commands.Range[int, 1],
                            resolution: Literal["posted", "retry", "skip"], acknowledge_duplicate_risk: bool,
                            message_id: str = ""):
        if await begin(interaction, services):
            if not acknowledge_duplicate_risk:
                await respond(interaction, "Check Discord first and explicitly acknowledge the duplicate-delivery risk.")
                return
            if resolution == "posted" and (not message_id.isascii() or not message_id.isdigit() or not 1 <= len(message_id) <= 20):
                await respond(interaction, "A numeric Discord message ID is required to record a posted receipt.")
                return
            item = storage.get_item_by_id(item_id)
            changed = item and storage.resolve_uncertain(item_id, resolution, message_id=message_id,
                                                          expected_revision=item["revision"], actor="owner")
            await respond(interaction, "Uncertainty resolved as requested." if changed else "Not resolved; inspect the current item state.")

    @feed.command(name="list", description="List configured sources without disclosing their feed URLs")
    async def feed_list(interaction: discord.Interaction):
        if await begin(interaction, services):
            sources = provider_runtime.get_configured_sources()
            lines = [f"{display_text(s.get('source_id', s.get('id')), 64)} · {display_text(s.get('name'), 100)} · "
                     f"{s.get('delivery_mode', 'review')} · {'enabled' if s.get('enabled', True) else 'disabled'}" for s in sources]
            await respond(interaction, "\n".join(lines) or "No sources configured.")

    @feed.command(name="add", description="Add a generic feed after checking that its terms permit your use")
    async def feed_add(interaction: discord.Interaction, source_id: str, name: str, url: str,
                       channel: discord.TextChannel, mode: Literal["automatic", "review"] = "review",
                       role: discord.Role | None = None, private: bool = True):
        if await begin(interaction, services):
            if channel.guild.id != config.HERALD_GUILD_ID or (role and role.guild.id != config.HERALD_GUILD_ID):
                await respond(interaction, "Channel and role must belong to the configured target server.")
                return
            if role:
                from subscriptions import SubscriptionDefinition, validate_subscription_role
                definition = SubscriptionDefinition(source_id, name, channel.id, role.id)
                _, problem = validate_subscription_role(interaction.guild, definition)
                if problem:
                    await respond(interaction, "The selected role is unsafe or unavailable for subscription use.")
                    return
                if any(source.get("role_id") == role.id for source in provider_runtime.get_configured_sources()):
                    await respond(interaction, "That role is already assigned to a source. Choose an unambiguous subscription role.")
                    return
            feed_config.add_feed({"id": source_id, "name": name, "url": url, "channel_id": channel.id,
                                  "delivery_mode": mode, "role_id": role.id if role else None, "private": private})
            await respond(interaction, "Feed saved atomically. Run /herald feed test or preview to inspect it.")

    @feed.command(name="remove", description="Remove a generic feed configuration while retaining queue history")
    async def feed_remove(interaction: discord.Interaction, source_id: str):
        if await begin(interaction, services):
            changed = feed_config.remove_feed(source_id)
            await respond(interaction, "Feed configuration removed; queue and audit history retained." if changed else "Generic feed not found.")

    @feed.command(name="enable", description="Enable a configured generic feed")
    async def feed_enable(interaction: discord.Interaction, source_id: str):
        if await begin(interaction, services):
            changed = feed_config.set_feed_enabled(source_id, True)
            await respond(interaction, "Feed enabled." if changed else "Generic feed not found.")

    @feed.command(name="disable", description="Disable future discovery from a generic feed")
    async def feed_disable(interaction: discord.Interaction, source_id: str):
        if await begin(interaction, services):
            changed = feed_config.set_feed_enabled(source_id, False)
            await respond(interaction, "Feed disabled. Existing queued items remain; inspect/skip them separately." if changed else "Generic feed not found.")

    async def preview_result(interaction, source_id, render):
        result = await provider_runtime.fetch_source_preview(source_id)
        health = result.get("health") or []
        states = [row.get("status") if row.get("status") in diagnostics.HEALTH_STATES else "failed" for row in health]
        items = result.get("items") or []
        text = f"Dry run: {len(items)} items; health: {', '.join(states) or 'unavailable'}. No queue or public post was created."
        if render and items:
            content, embed = services.build_post_payload_for_item(items[0], interaction.guild)
            # The destination and mention policy are not used by an ephemeral preview.
            await respond(interaction, text + "\n\n" + (content or ""), embed=embed)
        else:
            await respond(interaction, text)

    @feed.command(name="test", description="Fetch and validate one source without queuing or posting")
    async def feed_test(interaction: discord.Interaction, source_id: str):
        if await begin(interaction, services):
            await preview_result(interaction, source_id, False)

    @feed.command(name="preview", description="Privately preview one alert without queuing or posting")
    async def feed_preview(interaction: discord.Interaction, source_id: str):
        if await begin(interaction, services):
            await preview_result(interaction, source_id, True)

    @setup.command(name="subscriptions", description="Post a subscription select panel in the chosen channel")
    async def setup_subscriptions(interaction: discord.Interaction, channel: discord.TextChannel):
        if await begin(interaction, services):
            if channel.guild.id != config.HERALD_GUILD_ID:
                await respond(interaction, "Choose a channel in the configured target server.")
                return
            await services.post_subscription_panel(channel)
            await respond(interaction, "Subscription panel posted.")

    @setup.command(name="welcome-test", description="Post a welcome test for the owner in the configured welcome channel")
    async def welcome_test(interaction: discord.Interaction):
        if await begin(interaction, services):
            ok, detail = await services.post_welcome_for_member(interaction.user, event_type="owner_welcome_test")
            await respond(interaction, "Welcome test posted." if ok else "Welcome test unavailable. Check the welcome setting and channel permissions.")

    @audit.command(name="verify", description="Verify the local audit hash chain")
    async def audit_verify(interaction: discord.Interaction):
        if await begin(interaction, services):
            result = storage.verify_audit_chain()
            await respond(interaction, "Audit chain verified." if result.get("ok") else "Audit integrity verification failed.")

    @audit.command(name="recent", description="Show recent local audit evidence")
    async def audit_recent(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20] = 10):
        if await begin(interaction, services):
            await respond(interaction, storage.audit_recent_text(limit))

    @audit.command(name="summary", description="Summarise local audit history")
    async def audit_summary(interaction: discord.Interaction):
        if await begin(interaction, services):
            await respond(interaction, storage.audit_summary_text())

    @audit.command(name="item", description="Inspect audit history for one queue item")
    async def audit_item(interaction: discord.Interaction, item_id: app_commands.Range[int, 1]):
        if await begin(interaction, services):
            await respond(interaction, storage.audit_item_text(item_id))

    scope = getattr(config, "HERALD_COMMAND_SCOPE", "guild")
    tree.add_command(herald, guild=discord.Object(id=config.HERALD_GUILD_ID) if scope == "guild" else None)
    client._herald_tree = tree
    client._herald_registration_state = "registered"
    client._herald_registration_scope = scope
    client._herald_sync_lock = asyncio.Lock()
    return tree


async def synchronize_commands(client, services):
    tree = install_commands(client, services)
    async with client._herald_sync_lock:
        if client._herald_registration_state == "synced":
            return tree
        try:
            guild = discord.Object(id=config.HERALD_GUILD_ID)
            if client._herald_registration_scope == "guild":
                # Remove this bot's obsolete global registration when changing scope.
                await tree.sync(guild=None)
                await tree.sync(guild=guild)
            else:
                # Remove this bot's obsolete guild registration when changing scope.
                await tree.sync(guild=guild)
                await tree.sync(guild=None)
            client._herald_registration_state = "synced"
        except Exception:
            client._herald_registration_state = "sync_failed"
            raise RuntimeError("Herald command registration failed") from None
    return tree
