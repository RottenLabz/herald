import time
from urllib.parse import urlencode

import requests

from config import (
    TWITCH_CHANNELS,
    TWITCH_CLIENT_ID,
    TWITCH_CLIENT_SECRET,
    TWITCH_ENABLED,
    TWITCH_STREAMS_URL,
    TWITCH_TOKEN_URL,
    TWITCH_USER_AGENT,
)


_TOKEN_CACHE = {
    "access_token": "",
    "expires_at": 0,
}


def _now() -> int:
    return int(time.time())


def _get_app_access_token() -> str:
    if _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["expires_at"] > _now() + 60:
        return _TOKEN_CACHE["access_token"]

    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        raise RuntimeError("TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET is missing")

    response = requests.post(
        TWITCH_TOKEN_URL,
        data={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=30,
        headers={
            "User-Agent": TWITCH_USER_AGENT,
        },
    )
    response.raise_for_status()

    payload = response.json()

    access_token = str(payload.get("access_token") or "").strip()
    expires_in = int(payload.get("expires_in") or 0)

    if not access_token:
        raise RuntimeError("Twitch returned an empty access token")

    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = _now() + max(expires_in, 300)

    return access_token


def _stream_url(login_name: str) -> str:
    return f"https://www.twitch.tv/{login_name}"


def _thumbnail_url(raw_url: str, width: int = 640, height: int = 360) -> str:
    raw_url = str(raw_url or "").strip()

    if not raw_url:
        return ""

    return raw_url.replace("{width}", str(width)).replace("{height}", str(height))


def _make_summary(stream: dict) -> str:
    parts = []

    game_name = str(stream.get("game_name") or "").strip()
    viewer_count = stream.get("viewer_count")
    started_at = str(stream.get("started_at") or "").strip()
    language = str(stream.get("language") or "").strip()

    if game_name:
        parts.append(f"Playing: {game_name}")

    if viewer_count is not None:
        parts.append(f"Viewers: {viewer_count}")

    if language:
        parts.append(f"Language: {language}")

    if started_at:
        parts.append(f"Started: {started_at}")

    return " | ".join(parts)


def fetch_items() -> list[dict]:
    if not TWITCH_ENABLED:
        return []

    channels = list(dict.fromkeys(TWITCH_CHANNELS))

    if not channels:
        return []

    token = _get_app_access_token()

    query = urlencode(
        [("user_login", channel) for channel in channels],
        doseq=True,
    )

    response = requests.get(
        f"{TWITCH_STREAMS_URL}?{query}",
        timeout=30,
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": TWITCH_CLIENT_ID,
            "User-Agent": TWITCH_USER_AGENT,
        },
    )
    response.raise_for_status()

    payload = response.json()
    streams = payload.get("data") or []

    if not isinstance(streams, list):
        return []

    items = []

    for stream in streams:
        if not isinstance(stream, dict):
            continue

        stream_id = str(stream.get("id") or "").strip()
        login_name = str(stream.get("user_login") or "").strip().lower()
        display_name = str(stream.get("user_name") or login_name).strip()
        title = str(stream.get("title") or "").strip()
        game_name = str(stream.get("game_name") or "").strip()
        started_at = str(stream.get("started_at") or "").strip()
        thumbnail_url = _thumbnail_url(stream.get("thumbnail_url") or "")

        if not stream_id or not login_name:
            continue

        item_title = f"{display_name} is live on Twitch"

        if title:
            item_title = f"{display_name} is live: {title}"

        tags = ["stream_alerts", "twitch", "live"]

        if game_name:
            tags.append(game_name.lower().replace(" ", "_"))

        items.append(
            {
                "category": "stream_alerts",
                "source": "Twitch Helix",
                "title": item_title,
                "url": _stream_url(login_name),
                "summary": _make_summary(stream),
                "external_id": stream_id,
                "tags": sorted(set(tags)),
                "published_at": started_at,
                "feed_url": TWITCH_STREAMS_URL,
                "image_url": thumbnail_url,
            }
        )

    return items