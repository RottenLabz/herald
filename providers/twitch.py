import time

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


TWITCH_MAX_CHANNELS_PER_REQUEST = 100

_TOKEN_CACHE = {
    "expires_at": 0,
}


def _now() -> int:
    return int(time.time())


def _clear_token_cache() -> None:
    _TOKEN_CACHE.pop("access_token", None)
    _TOKEN_CACHE["expires_at"] = 0


def _get_app_access_token() -> str:
    cached_token = str(_TOKEN_CACHE.get("access_token") or "")

    if cached_token and _TOKEN_CACHE["expires_at"] > _now() + 60:
        return cached_token

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

    if not isinstance(payload, dict):
        raise RuntimeError("Twitch returned an invalid token response")

    access_token = str(payload.get("access_token") or "").strip()
    expires_in = int(payload.get("expires_in") or 0)

    if not access_token:
        raise RuntimeError("Twitch returned an empty access token")

    _TOKEN_CACHE["access_token"] = access_token
    _TOKEN_CACHE["expires_at"] = _now() + max(expires_in, 0)

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


def _channel_batches(channels: list[str]) -> list[list[str]]:
    return [
        channels[index:index + TWITCH_MAX_CHANNELS_PER_REQUEST]
        for index in range(0, len(channels), TWITCH_MAX_CHANNELS_PER_REQUEST)
    ]


def _get_streams_response(channels: list[str], token: str) -> requests.Response:
    params = [("user_login", channel) for channel in channels]
    params.append(("first", str(TWITCH_MAX_CHANNELS_PER_REQUEST)))

    return requests.get(
        TWITCH_STREAMS_URL,
        params=params,
        timeout=30,
        headers={
            "Authorization": f"Bearer {token}",
            "Client-Id": TWITCH_CLIENT_ID,
            "User-Agent": TWITCH_USER_AGENT,
        },
    )


def _fetch_stream_batch(channels: list[str], token: str) -> tuple[list[dict], str]:
    response = _get_streams_response(channels, token)

    # Twitch recommends reacting to a 401 because tokens can become invalid
    # before their advertised expiry. App tokens cannot be refreshed, so obtain
    # a new app token and retry this batch once.
    if response.status_code == 401:
        _clear_token_cache()
        token = _get_app_access_token()
        response = _get_streams_response(channels, token)

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Twitch returned an invalid streams response")

    streams = payload.get("data") or []

    if not isinstance(streams, list):
        raise RuntimeError("Twitch returned a non-list streams payload")

    return streams, token


def fetch_items() -> list[dict]:
    if not TWITCH_ENABLED:
        return []

    channels = list(dict.fromkeys(TWITCH_CHANNELS))

    if not channels:
        return []

    token = _get_app_access_token()
    streams = []

    for batch in _channel_batches(channels):
        batch_streams, token = _fetch_stream_batch(batch, token)
        streams.extend(batch_streams)

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
                # A channel URL is permanent and reused for every broadcast.
                # Twitch stream IDs, not channel URLs, identify alert events.
                "dedupe_by_url": False,
            }
        )

    return items
