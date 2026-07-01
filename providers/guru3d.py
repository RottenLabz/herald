import re
from html import unescape

import feedparser

from config import GURU3D_RSS_URL


def clean_text(text: str, max_chars: int = 500) -> str:
    text = text or ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text


def is_gpu_driver_item(item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('url', '')} {item.get('summary', '')}".lower()

    driver_words = [
        "driver",
        "drivers",
        "hotfix",
        "whql",
    ]

    gpu_words = [
        "nvidia",
        "geforce",
        "amd",
        "radeon",
        "adrenalin",
        "intel",
        "arc",
    ]

    return (
        any(word in text for word in driver_words)
        and any(word in text for word in gpu_words)
    )


def make_tags(item: dict) -> list[str]:
    text = f"{item.get('title', '')} {item.get('url', '')} {item.get('summary', '')}".lower()
    tags = {"gpu_updates", "driver", "guru3d"}

    rules = {
        "amd": ["amd", "radeon", "adrenalin"],
        "nvidia": ["nvidia", "geforce", "game ready"],
        "intel": ["intel", "arc"],
        "whql": ["whql"],
        "hotfix": ["hotfix"],
    }

    for tag, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            tags.add(tag)

    return sorted(tags)


def fetch_items() -> list[dict]:
    parsed = feedparser.parse(GURU3D_RSS_URL)
    items = []

    for entry in parsed.entries[:30]:
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

        item = {
            "category": "gpu_updates",
            "source": "Guru3D RSS",
            "title": title,
            "url": url,
            "summary": summary,
            "external_id": str(external_id).strip(),
            "tags": [],
            "published_at": published_at,
            "feed_url": GURU3D_RSS_URL,
        }

        item["tags"] = make_tags(item)

        if is_gpu_driver_item(item):
            items.append(item)

    return items
