"""The sole concrete bundled provider. Claim URL and GamerPower attribution are separate."""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from providers.common import (MAX_ENTRIES, OPERATION_TIMEOUT, clean_text, error_code,
                              fetch_bytes, health, normalize_item, split_tags)
from providers import rss

MOBILE_KEYWORDS = ("android", "ios", "iphone", "ipad", "mobile", "google play", "app store")
GIVEAWAY_NOISE_KEYWORDS = ("credits", "currency", "coins", "gems", "gift pack", "starter pack",
                         "weapon skin", "skin giveaway", "cosmetic", "booster", "loot", "playtest",
                         "closed beta", "beta key", "anniversary weapon", "bundle key giveaway")


def gamerpower_aliases(*values) -> list[str]:
    """Canonical aliases for one GamerPower giveaway across API/RSS transports."""
    aliases = []

    for value in values:
        text = str(value or "").strip()
        if not text:
            continue

        try:
            parts = urlsplit(text)
        except ValueError:
            continue

        if (parts.hostname or "").lower() not in {"gamerpower.com", "www.gamerpower.com"}:
            continue

        path = parts.path or "/"

        if path.startswith("/open/"):
            path = "/" + path[len("/open/"):].lstrip("/")

        if path == "/":
            continue

        canonical = "https://www.gamerpower.com" + path
        claim = "https://www.gamerpower.com/open/" + path.lstrip("/")

        for alias in (canonical, claim):
            if alias not in aliases:
                aliases.append(alias)

    return aliases[:4]


def configured_source() -> dict:
    import config
    return {"provider_id": "gamerpower", "source_id": "gamerpower", "id": "gamerpower", "name": "GamerPower",
            "url": config.GAMERPOWER_API_URL, "enabled": config.FREE_GAMES_ENABLED,
            "channel_id": getattr(config, "FREE_GAMES_CHANNEL_ID", 0),
            "role_id": getattr(config, "FREE_GAMES_ROLE_ID", 0) or None,
            "delivery_mode": getattr(config, "FREE_GAMES_DELIVERY_MODE", "automatic"),
            "category": "free_games", "homepage_url": "https://www.gamerpower.com/",
            "attribution_label": "GamerPower", "attribution_url": "https://www.gamerpower.com/",
            "tags": ["free_games", "free_game", "gamerpower"], "private": False}


def make_tags(item: dict) -> list[str]:
    text = " ".join([clean_text(item.get("title")), clean_text(item.get("summary")),
                     " ".join(split_tags(item.get("tags")))]).lower()
    tags = {"free_games", "free_game", "gamerpower"}
    for name, words in {"steam": ["steam"], "epic": ["epic games", "epic store"],
                        "gog": ["gog", "good old games"], "itch": ["itch.io"],
                        "pc": ["pc", "windows"], "drm_free": ["drm-free", "drm free"]}.items():
        if any(word in text for word in words):
            tags.add(name)
    tags.update(t.lower().replace(" ", "_") for t in split_tags(item.get("tags")))
    return sorted(tags)[:16]


def is_mobile_item(item: dict) -> bool:
    platforms = " ".join(split_tags(item.get("tags"))).lower()
    text = platforms or (clean_text(item.get("title")) + " " + clean_text(item.get("summary"))).lower()
    # Token boundaries prevent iOS from matching BioShock or Studios.
    return any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) for word in MOBILE_KEYWORDS)


def is_noisy_giveaway_item(item: dict) -> bool:
    import config
    if not config.HERALD_FREE_GAME_STRICT_FILTER:
        return False
    text = (clean_text(item.get("title")) + " " + clean_text(item.get("summary"))).lower()
    return any(word in text for word in GIVEAWAY_NOISE_KEYWORDS)


def fetch_api_result(source: dict, *, deadline=None) -> dict:
    deadline = deadline or (time.monotonic() + OPERATION_TIMEOUT)
    payload = json.loads(fetch_bytes(source["url"], deadline=deadline))
    if not isinstance(payload, list):
        raise ValueError("invalid_api_payload")
    items, invalid = [], 0
    for game in payload[:MAX_ENTRIES]:
        if not isinstance(game, dict):
            invalid += 1
            continue
        try:
            raw = {"title": game.get("title"),
                   "url": game.get("open_giveaway_url") or game.get("gamerpower_url"),
                   "attribution_url": game.get("gamerpower_url", ""),
                   "summary": clean_text(game.get("description"), 1000) + " " + clean_text(game.get("instructions"), 400),
                   "external_id": game.get("id"),
                   "dedupe_urls": gamerpower_aliases(
                       game.get("open_giveaway_url"),
                       game.get("gamerpower_url"),
                   ),
                   "tags": split_tags(game.get("platforms")),
                   "published_at": game.get("published_date") or game.get("created_at") or "",
                   "image_url": game.get("thumbnail") or game.get("image") or ""}
            if is_mobile_item(raw) or is_noisy_giveaway_item(raw):
                continue
            raw["tags"] = make_tags(raw)
            items.append(normalize_item(raw, source))
        except (ValueError, TypeError):
            invalid += 1
    error = "invalid_entries" if invalid else "entry_limit_reached" if len(payload) > MAX_ENTRIES else ""
    status = "degraded" if error and items else "failed" if error else "healthy" if items else "empty"
    return {"items": items, "health": health(source, status, len(items), error)}


def fetch_source(source: dict | None = None) -> dict:
    import config
    source = source or configured_source()
    if not source["enabled"]:
        return {"items": [], "health": health(source, "disabled")}
    deadline = time.monotonic() + OPERATION_TIMEOUT
    try:
        result = fetch_api_result(source, deadline=deadline)
        # A valid empty feed is a successful observation, not a failed retrieval.
        if result["health"]["status"] != "failed":
            return result
        api_error = result["health"]["error"]
    except Exception as exc:
        api_error = error_code(exc)

    if not config.HERALD_GAMERPOWER_RSS_FALLBACK_ENABLED:
        return {
            "items": [],
            "health": health(
                source,
                "failed",
                error="api_failed_no_rss_fallback:" + api_error,
            ),
        }

    fallback = dict(source, url=config.GAMERPOWER_RSS_URL)
    result = rss.fetch_source(fallback, deadline=deadline)
    if result["health"]["status"] in {"healthy", "empty", "degraded"}:
        filtered = []
        prefix = "https://www.gamerpower.com/"
        for item in result["items"]:
            if is_mobile_item(item) or is_noisy_giveaway_item(item):
                continue
            item = dict(item)
            aliases = gamerpower_aliases(
                item.get("url"),
                item.get("attribution_url"),
            )
            if aliases:
                # RSS uses the canonical GamerPower page; API keeps the /open/
                # claim URL. Both identities remain explicit aliases.
                item["url"] = aliases[0]
            item["dedupe_urls"] = aliases
            item["preserve_existing_on_dedupe_match"] = True
            filtered.append(item)
        result["items"] = filtered
        result["health"] = health(source, "degraded", len(result["items"]), "api_failed_rss_fallback:" + api_error)
    else:
        result["health"] = health(source, "failed", error="api_and_rss_failed:" + api_error)
    return result


def fetch_api_items() -> list[dict]:
    return fetch_api_result(configured_source())["items"]


def fetch_rss_items() -> list[dict]:
    import config
    source = dict(configured_source(), url=config.GAMERPOWER_RSS_URL)
    result = rss.fetch_source(source)
    if result["health"]["status"] == "failed":
        raise ValueError("GamerPower RSS failed")
    return result["items"]


def fetch_items() -> list[dict]:
    result = fetch_source()
    if result["health"]["status"] == "failed":
        raise ValueError("GamerPower fetch failed")
    return result["items"]
