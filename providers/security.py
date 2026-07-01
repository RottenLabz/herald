import hashlib
import re
from html import unescape

import feedparser

from config import (
    HERALD_SECURITY_STRICT_FILTER,
    SECURITY_RSS_URLS,
)


SECURITY_IMPORTANT_KEYWORDS = [
    "actively exploited",
    "exploited in the wild",
    "zero-day",
    "zero day",
    "critical",
    "emergency",
    "ransomware",
    "cve-",
    "vulnerability",
    "vulnerabilities",
    "patch",
    "security update",
    "malware",
    "phishing",
    "data breach",
    "breach",
    "supply-chain",
    "supply chain",
    "microsoft",
    "windows",
    "chrome",
    "firefox",
    "github",
    "discord",
    "steam",
    "vpn",
    "router",
    "ubiquiti",
    "citrix",
    "cisco",
    "fortinet",
    "sonicwall",
    "openssl",
    "linux",
    "android",
    "ios",
]

SECURITY_NOISE_KEYWORDS = [
    "sponsored",
    "webinar",
    "whitepaper",
    "ebook",
    "buyer guide",
    "promotion",
    "discount",
    "deal",
    "course",
    "training",
    "certification",
    "podcast",
]


def clean_text(text: str, max_chars: int = 650) -> str:
    text = text or ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."

    return text


def normalise_source(feed_url: str, feed_title: str = "") -> str:
    text = f"{feed_title} {feed_url}".lower()

    if "cisa.gov" in text:
        return "CISA"

    if "bleepingcomputer.com" in text:
        return "BleepingComputer"

    if "ncsc.gov.uk" in text:
        return "UK NCSC"

    return feed_title.strip() or "Security RSS"


def make_external_id(source: str, url: str, title: str, published_at: str) -> str:
    raw = "|".join([source, url, title, published_at]).encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:32]


def item_text(item: dict) -> str:
    return " ".join([
        item.get("source", ""),
        item.get("title", ""),
        item.get("url", ""),
        item.get("summary", ""),
        " ".join(item.get("tags") or []),
    ]).lower()


def security_tags_for_item(item: dict) -> list[str]:
    text = item_text(item)
    tags = {"security"}

    tag_rules = {
        "cve": ["cve-"],
        "critical": ["critical", "emergency"],
        "exploited": ["actively exploited", "exploited in the wild"],
        "zero_day": ["zero-day", "zero day"],
        "ransomware": ["ransomware"],
        "malware": ["malware"],
        "breach": ["data breach", "breach"],
        "patch": ["patch", "security update"],
        "windows": ["microsoft", "windows"],
        "browser": ["chrome", "firefox", "edge", "browser"],
        "network": ["router", "vpn", "cisco", "fortinet", "sonicwall", "ubiquiti"],
        "linux": ["linux", "openssl"],
        "gaming_adjacent": ["discord", "steam", "github"],
    }

    for tag, keywords in tag_rules.items():
        if any(keyword in text for keyword in keywords):
            tags.add(tag)

    return sorted(tags)


def is_security_item(item: dict) -> bool:
    if not HERALD_SECURITY_STRICT_FILTER:
        return True

    text = item_text(item)

    if any(keyword in text for keyword in SECURITY_NOISE_KEYWORDS):
        return False

    return any(keyword in text for keyword in SECURITY_IMPORTANT_KEYWORDS)


def fetch_feed_items(feed_url: str) -> list[dict]:
    parsed = feedparser.parse(feed_url)
    feed_title = getattr(parsed.feed, "title", "") if getattr(parsed, "feed", None) else ""
    source = normalise_source(feed_url, feed_title)

    items = []

    for entry in parsed.entries[:12]:
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()

        if not title or not url:
            continue

        raw_summary = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )

        summary = clean_text(raw_summary)

        published_at = (
            getattr(entry, "published", "")
            or getattr(entry, "updated", "")
            or ""
        ).strip()

        external_id = (
            getattr(entry, "id", "")
            or getattr(entry, "guid", "")
            or make_external_id(source, url, title, published_at)
        )

        item = {
            "category": "security_alerts",
            "source": source,
            "title": title,
            "url": url,
            "feed_url": feed_url,
            "summary": summary,
            "published_at": published_at,
            "tags": [],
            "external_id": str(external_id).strip(),
        }

        item["tags"] = security_tags_for_item(item)

        if is_security_item(item):
            items.append(item)

    return items


def fetch_items() -> list[dict]:
    items = []
    errors = []

    for feed_url in SECURITY_RSS_URLS:
        try:
            items.extend(fetch_feed_items(feed_url))
        except Exception as e:
            errors.append(f"{feed_url}: {e}")

    if errors:
        print(
            "Security RSS fetch errors: " + " | ".join(errors[:5]),
            flush=True,
        )

    return items
