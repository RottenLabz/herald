import re
from html import unescape

import feedparser
import requests

from config import (
    GAMERPOWER_API_URL,
    GAMERPOWER_RSS_URL,
    HERALD_FREE_GAME_STRICT_FILTER,
)


MOBILE_KEYWORDS = [
    "android",
    "ios",
    "iphone",
    "ipad",
    "mobile",
    "google play",
    "app store",
]

GIVEAWAY_NOISE_KEYWORDS = [
    "credits",
    "currency",
    "coins",
    "gems",
    "gift pack",
    "starter pack",
    "weapon skin",
    "weapon skins",
    "skin giveaway",
    "skins giveaway",
    "cosmetic",
    "cosmetics",
    "booster",
    "loot",
    "loot pack",
    "in-game loot",
    "playtest",
    "closed beta",
    "beta key",
    "anniversary weapon",
    "bundle key giveaway",
]


def clean_text(text: str, max_chars: int = 500) -> str:
    text = text or ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text


def split_tags(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        raw_tags = value
    elif isinstance(value, str):
        raw_tags = re.split(r"[,;/|]+", value)
    else:
        raw_tags = [str(value)]

    tags = []
    for tag in raw_tags:
        text = str(tag).strip()
        if text:
            tags.append(text)

    return sorted(set(tags))


def make_tags(item: dict) -> list[str]:
    text = " ".join([
        item.get("category", ""),
        item.get("source", ""),
        item.get("title", ""),
        item.get("url", ""),
        item.get("summary", ""),
        " ".join(split_tags(item.get("tags"))),
    ]).lower()

    tags = {"free_games", "free_game", "gamerpower"}

    rules = {
        "steam": ["steam", "steampowered"],
        "epic": ["epic games", "epic store"],
        "gog": ["gog", "good old games"],
        "itch": ["itch.io", "itchio"],
        "pc": ["pc", "windows"],
        "drm_free": ["drm-free", "drm free"],
    }

    for tag, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            tags.add(tag)

    for tag in split_tags(item.get("tags")):
        tags.add(tag.lower().replace(" ", "_"))

    return sorted(tags)


def is_mobile_item(item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('url', '')} {item.get('summary', '')} {' '.join(split_tags(item.get('tags')))}".lower()
    return any(keyword in text for keyword in MOBILE_KEYWORDS)


def is_noisy_giveaway_item(item: dict) -> bool:
    if not HERALD_FREE_GAME_STRICT_FILTER:
        return False

    text = f"{item.get('title', '')} {item.get('url', '')} {item.get('summary', '')} {' '.join(split_tags(item.get('tags')))}".lower()
    return any(keyword in text for keyword in GIVEAWAY_NOISE_KEYWORDS)


def fetch_api_items() -> list[dict]:
    response = requests.get(GAMERPOWER_API_URL, timeout=30)
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list):
        raise ValueError("GamerPower API returned a non-list payload")

    items = []

    for game in payload[:30]:
        if not isinstance(game, dict):
            continue

        external_id = str(game.get("id") or "").strip()

        url = (
            str(game.get("open_giveaway_url") or "").strip()
            or str(game.get("gamerpower_url") or "").strip()
            or (
                f"https://www.gamerpower.com/api/giveaway?id={external_id}"
                if external_id
                else ""
            )
        )

        title = str(game.get("title") or "").strip()

        if not title or not url:
            continue

        description = clean_text(game.get("description") or "")
        instructions = clean_text(game.get("instructions") or "", 250)

        summary_parts = []

        if description:
            summary_parts.append(description)

        if instructions:
            summary_parts.append(f"Instructions: {instructions}")

        metadata_parts = []

        worth = str(game.get("worth") or "").strip()
        end_date = str(game.get("end_date") or "").strip()
        status = str(game.get("status") or "").strip()

        if worth:
            metadata_parts.append(f"Worth: {worth}")

        if end_date:
            metadata_parts.append(f"Ends: {end_date}")

        if status:
            metadata_parts.append(f"Status: {status}")

        if metadata_parts:
            summary_parts.append(" | ".join(metadata_parts))

        image_url = (
            str(game.get("thumbnail") or "").strip()
            or str(game.get("image") or "").strip()
        )

        item = {
            "category": "free_games",
            "source": "GamerPower API",
            "title": title,
            "url": url,
            "summary": clean_text("\n\n".join(summary_parts)),
            "external_id": external_id,
            "tags": split_tags(game.get("platforms")),
            "published_at": str(game.get("published_date") or game.get("created_at") or "").strip(),
            "feed_url": GAMERPOWER_API_URL,
            "image_url": image_url,
        }

        item["tags"] = make_tags(item)
        items.append(item)

    return [
        item
        for item in items
        if not is_mobile_item(item)
        and not is_noisy_giveaway_item(item)
    ]


def fetch_rss_items() -> list[dict]:
    parsed = feedparser.parse(GAMERPOWER_RSS_URL)
    items = []

    for entry in parsed.entries[:20]:
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()

        if not title or not url:
            continue

        summary = clean_text(
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )

        published_at = (
            getattr(entry, "published", "")
            or getattr(entry, "updated", "")
            or ""
        ).strip()

        external_id = (
            getattr(entry, "id", "")
            or getattr(entry, "guid", "")
            or url
        )

        image_url = ""

        if getattr(entry, "media_thumbnail", None):
            try:
                image_url = entry.media_thumbnail[0].get("url", "")
            except Exception:
                image_url = ""

        if not image_url and getattr(entry, "media_content", None):
            try:
                image_url = entry.media_content[0].get("url", "")
            except Exception:
                image_url = ""

        item = {
            "category": "free_games",
            "source": "GamerPower RSS",
            "title": title,
            "url": url,
            "summary": summary,
            "external_id": str(external_id).strip(),
            "tags": ["free_games", "free_game", "gamerpower"],
            "published_at": published_at,
            "feed_url": GAMERPOWER_RSS_URL,
            "image_url": image_url,
        }

        item["tags"] = make_tags(item)
        items.append(item)

    return [
        item
        for item in items
        if not is_mobile_item(item)
        and not is_noisy_giveaway_item(item)
    ]


def fetch_items() -> list[dict]:
    try:
        api_items = fetch_api_items()

        if api_items:
            return api_items

        print("GamerPower API returned no items; falling back to RSS.", flush=True)

    except Exception as e:
        print(f"GamerPower API failed; falling back to RSS: {e}", flush=True)

    return fetch_rss_items()
