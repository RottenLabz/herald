"""Bounded retrieval and normalization shared by every public content provider."""
from __future__ import annotations

import hashlib
import html
import re
import time
from urllib.parse import urlsplit


from feed_config import FeedConfigError, MAX_URL, validate_url

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 5.0
OPERATION_TIMEOUT = 20.0
MAX_BODY_BYTES = 1024 * 1024
MAX_ENTRIES = 50
MAX_RAW_TEXT = 16384
MAX_TITLE = 256
MAX_SUMMARY = 1500
MAX_ERROR = 160
MAX_TAGS = 16
MAX_TAG = 32
MAX_EXTERNAL_ID = 256


class ProviderBoundaryError(ValueError):
    pass


def error_code(exc: BaseException) -> str:
    # Exception strings from HTTP/parser/plugin libraries may contain credentials.
    if isinstance(exc, ProviderBoundaryError):
        code = str(exc)
        if code in {"operation_deadline_exceeded", "decoded_body_limit_exceeded", "invalid_item_type", "missing_item_title", "invalid_item_url"} or re.fullmatch(r"http_status_[1-5][0-9]{2}", code):
            return code
    return ("provider_error:" + type(exc).__name__)[:MAX_ERROR]


def fetch_bytes(url: str, *, deadline: float | None = None, session=None) -> bytes:
    url = validate_url(url)
    deadline = deadline or (time.monotonic() + OPERATION_TIMEOUT)
    if session is None:
        import requests
        session = requests
    client = session
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderBoundaryError("operation_deadline_exceeded")
    # Streamed iter_content yields decoded bytes, including compressed responses.
    # Parent worker process enforces an absolute deadline over DNS/connect/read/parse.
    with client.get(url, stream=True, timeout=(min(CONNECT_TIMEOUT, remaining), min(READ_TIMEOUT, remaining)),
                    allow_redirects=False, headers={"User-Agent": "RottenLabz-Herald/0.2.0"}) as response:
        if not 200 <= response.status_code < 300:
            raise ProviderBoundaryError(f"http_status_{response.status_code}")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            if time.monotonic() > deadline:
                raise ProviderBoundaryError("operation_deadline_exceeded")
            if not chunk:
                continue
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise ProviderBoundaryError("decoded_body_limit_exceeded")
            body.extend(chunk)
        if time.monotonic() > deadline:
            raise ProviderBoundaryError("operation_deadline_exceeded")
    return bytes(body)


def bounded_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return str(value)[:limit]


def clean_text(value: object, max_chars: int = MAX_SUMMARY) -> str:
    """Linear tag removal on a pre-bounded raw string; no unbounded HTML regex."""
    raw = html.unescape(bounded_text(value, MAX_RAW_TEXT))
    out = []
    in_tag = False
    for char in raw:
        if char == "<":
            in_tag = True
            out.append(" ")
        elif char == ">" and in_tag:
            in_tag = False
            out.append(" ")
        elif not in_tag:
            if char.isspace():
                out.append(" ")
            elif ord(char) >= 32 and ord(char) != 127:
                out.append(char)
    return " ".join("".join(out).split())[:max_chars].strip()


def escape_display_text(value: object, limit: int = MAX_SUMMARY) -> str:
    # Plain literal text; URLs are added separately by Herald's renderer.
    text = bounded_text(value, limit)
    text = text.replace("@", "@\u200b")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~])", r"\\\1", text)


def safe_url(value: object, *, optional: bool = False) -> str:
    if not isinstance(value, str):
        if optional:
            return ""
        raise ProviderBoundaryError("invalid_item_url")
    try:
        return validate_url(value, optional=optional)
    except FeedConfigError:
        if optional:
            return ""
        raise ProviderBoundaryError("invalid_item_url") from None


def split_tags(value: object) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[,;/|]+", value[:MAX_RAW_TEXT], maxsplit=MAX_TAGS)
    elif isinstance(value, list):
        raw = value[:MAX_TAGS]
    else:
        raw = []
    return list(dict.fromkeys(clean_text(tag, MAX_TAG) for tag in raw if clean_text(tag, MAX_TAG)))[:MAX_TAGS]


def bounded_identity(value: object) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ProviderBoundaryError("invalid_external_id")
    text = str(value).strip()
    if len(text) > MAX_BODY_BYTES:
        raise ProviderBoundaryError("external_id_limit_exceeded")
    # Never use HTML cleaning/display truncation for an identity. Reserve the
    # digest prefix so a literal provider ID cannot alias a long hashed ID.
    if len(text) > MAX_EXTERNAL_ID or text.startswith("sha256:"):
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text


def normalize_item(item: dict, source: dict) -> dict:
    if not isinstance(item, dict):
        raise ProviderBoundaryError("invalid_item_type")
    title = clean_text(item.get("title"), MAX_TITLE)
    if not title:
        raise ProviderBoundaryError("missing_item_title")
    url = safe_url(item.get("url"))
    source_id = source["id"]
    attribution_label = clean_text(source.get("attribution_label", ""), 100)
    attribution_url = safe_url(source.get("attribution_url", ""), optional=True)
    if source.get("provider_id") == "gamerpower":
        attribution_label = "GamerPower"
        candidate = safe_url(item.get("attribution_url", ""), optional=True)
        hostname = (urlsplit(candidate).hostname or "").lower()
        attribution_url = candidate if hostname in {"gamerpower.com", "www.gamerpower.com"} else "https://www.gamerpower.com/"
    return {
        "provider_id": source.get("provider_id", "rss"),
        "source_id": source_id,
        "source_policy_digest": source.get("policy_digest", ""),
        "source": clean_text(source["name"], 100),
        "category": clean_text(source.get("category", "announcements"), 64),
        "delivery_mode": source["delivery_mode"],
        "destination_channel_id": source.get("channel_id") or 0,
        "subscription_role_id": source.get("role_id") or 0,
        "source_url": safe_url(source.get("homepage_url", ""), optional=True),
        "private": bool(source.get("private", False)),
        "title": title,
        "url": url,
        "summary": clean_text(item.get("summary"), MAX_SUMMARY),
        "tags": split_tags(split_tags(source.get("tags", [])) + split_tags(item.get("tags", []))),
        "external_id": bounded_identity(item.get("external_id") or url),
        "published_at": clean_text(item.get("published_at"), 100),
        "image_url": safe_url(item.get("image_url", ""), optional=True),
        "feed_url": safe_url(source.get("url", ""), optional=True),
        "attribution_label": attribution_label,
        "attribution_url": attribution_url,
    }


def health(source: dict, status: str, count: int = 0, error: str = "") -> dict:
    return {"source_id": source["id"], "name": "Private source" if source.get("private") else clean_text(source["name"], 100),
            "status": status, "item_count": count, "error": error[:MAX_ERROR]}
