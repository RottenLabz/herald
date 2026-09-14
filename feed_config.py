"""Versioned, validated runtime RSS configuration. No network I/O or code loading."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_VERSION = 1
MAX_CONFIG_BYTES = 262144
MAX_FEEDS = 32
MAX_URL = 2048
MAX_TAGS = 16
_CONFIG_LOCK = threading.RLock()
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_FIELDS = {"id", "name", "url", "enabled", "channel_id", "delivery_mode", "role_id",
           "category", "homepage_url", "attribution_label", "attribution_url", "tags", "private"}


class FeedConfigError(ValueError):
    """Messages contain field names only, never values or private URLs."""


def validate_url(value: object, *, optional: bool = False) -> str:
    if optional and (value is None or value == ""):
        return ""
    if not isinstance(value, str) or not value or len(value) > MAX_URL:
        raise FeedConfigError("URL missing or exceeds limit")
    if any(ord(c) < 33 or ord(c) == 127 or c in '<>\\' for c in value):
        raise FeedConfigError("URL contains unsafe characters")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
    except ValueError:
        raise FeedConfigError("URL must be HTTP(S), with a host and no embedded credentials") from None
    return value


def _text(value: object, field: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise FeedConfigError(f"Invalid {field}")
    value = value.strip()
    if required and not value:
        raise FeedConfigError(f"Missing {field}")
    return value


def _snowflake(value: object, field: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value < 2**64:
        raise FeedConfigError(f"Invalid {field}")
    return value


def validate_feed(feed: object) -> dict:
    if not isinstance(feed, dict) or set(feed) - _FIELDS:
        raise FeedConfigError("Invalid feed fields")
    source_id = feed.get("id", "")
    if not isinstance(source_id, str) or not _ID.fullmatch(source_id):
        raise FeedConfigError("Invalid source id")
    if source_id == "gamerpower":
        raise FeedConfigError("Reserved source id")
    for field, default in (("enabled", True), ("private", False)):
        if not isinstance(feed.get(field, default), bool):
            raise FeedConfigError(f"Invalid {field}: expected boolean")
    mode = feed.get("delivery_mode", "review")
    if not isinstance(mode, str) or mode not in {"automatic", "review"}:
        raise FeedConfigError("Invalid delivery_mode")
    tags = feed.get("tags", [])
    if not isinstance(tags, list) or len(tags) > MAX_TAGS:
        raise FeedConfigError("Invalid tags")
    tags = [_text(t, "tag", 32, required=True) for t in tags]
    attribution_label = _text(feed.get("attribution_label", ""), "attribution_label", 100)
    attribution_url = validate_url(feed.get("attribution_url", ""), optional=True)
    if bool(attribution_label) != bool(attribution_url):
        raise FeedConfigError("Attribution label and URL must be provided together")
    return {
        "id": source_id,
        "name": _text(feed.get("name", ""), "name", 100, required=True),
        "url": validate_url(feed.get("url")),
        "enabled": feed.get("enabled", True),
        "channel_id": _snowflake(feed.get("channel_id"), "channel_id"),
        "delivery_mode": mode,
        "role_id": _snowflake(feed.get("role_id"), "role_id", optional=True),
        "category": _text(feed.get("category", "announcements"), "category", 64, required=True),
        "homepage_url": validate_url(feed.get("homepage_url", ""), optional=True),
        "attribution_label": attribution_label,
        "attribution_url": attribution_url,
        "tags": list(dict.fromkeys(tags)),
        "private": feed.get("private", False),
    }


def validate_config(config: object) -> dict:
    if not isinstance(config, dict) or set(config) != {"version", "feeds"}:
        raise FeedConfigError("Config must contain version and feeds")
    if type(config["version"]) is not int or config["version"] != CONFIG_VERSION:
        raise FeedConfigError("Unsupported feed config version")
    if not isinstance(config["feeds"], list) or len(config["feeds"]) > MAX_FEEDS:
        raise FeedConfigError("Invalid feeds list or feed count exceeds limit")
    feeds = [validate_feed(feed) for feed in config["feeds"]]
    if len({feed["id"] for feed in feeds}) != len(feeds):
        raise FeedConfigError("Duplicate source id")
    return {"version": CONFIG_VERSION, "feeds": feeds}


def config_path(path=None) -> Path:
    if path is None:
        import config
        path = getattr(config, "HERALD_FEED_CONFIG_PATH", "./data/feeds.json")
    return Path(path)


def load_config(path=None) -> dict:
    target = config_path(path)
    try:
        with target.open("rb") as stream:
            data = stream.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return {"version": CONFIG_VERSION, "feeds": []}
    except OSError:
        raise FeedConfigError("Feed config cannot be read") from None
    if len(data) > MAX_CONFIG_BYTES:
        raise FeedConfigError("Feed config exceeds byte limit")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise FeedConfigError("Feed config JSON is invalid") from None
    return validate_config(value)


def save_config(value: dict, path=None) -> dict:
    validated = validate_config(value)
    encoded = (json.dumps(validated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise FeedConfigError("Feed config exceeds byte limit")
    target = config_path(path)
    # Parent must be an operator-provisioned runtime directory. Never create paths.
    with _CONFIG_LOCK:
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".feeds-", suffix=".tmp", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                if hasattr(os, "fchmod"):
                    os.fchmod(stream.fileno(), 0o600)
                # Windows operators protect the runtime directory using native ACLs.
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError:
            raise FeedConfigError("Feed config write failed; verify current config before retrying") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
    return validated


def list_feeds(path=None) -> list[dict]:
    return load_config(path)["feeds"]


def add_feed(feed: dict, path=None) -> dict:
    validated = validate_feed(feed)
    with _CONFIG_LOCK:
        current = load_config(path)
        if any(f["id"] == validated["id"] for f in current["feeds"]):
            raise FeedConfigError("Source id already exists")
        current["feeds"].append(validated)
        save_config(current, path)
    return validated


def remove_feed(source_id: str, path=None) -> bool:
    with _CONFIG_LOCK:
        current = load_config(path)
        retained = [f for f in current["feeds"] if f["id"] != source_id]
        if len(retained) == len(current["feeds"]):
            return False
        current["feeds"] = retained
        save_config(current, path)
    return True


def set_feed_enabled(source_id: str, enabled: bool, path=None) -> bool:
    if type(enabled) is not bool:
        raise FeedConfigError("Invalid enabled: expected boolean")
    with _CONFIG_LOCK:
        current = load_config(path)
        for feed in current["feeds"]:
            if feed["id"] == source_id:
                feed["enabled"] = enabled
                save_config(current, path)
                return True
    return False
