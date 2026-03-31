from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import requests

from app.services.market_history import ensure_history_and_cache

BASE_URL = "https://prices.runescape.wiki/api/v1/osrs"
USER_AGENT = "osrs-flip-assistant/0.1"

_mapping_cache: list[dict[str, Any]] | None = None
_snapshot_cache: dict[str, Any] | None = None
_snapshot_cache_bucket: str | None = None


def _get_json(endpoint: str) -> dict[str, Any] | list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _current_bucket() -> str:
    now = datetime.now(UTC)
    minute = (now.minute // 5) * 5
    return now.replace(minute=minute, second=0, microsecond=0).isoformat()


def get_mapping() -> list[dict[str, Any]]:
    global _mapping_cache
    if _mapping_cache is not None:
        return _mapping_cache

    data = _get_json("mapping")
    _mapping_cache = data if isinstance(data, list) else []
    return _mapping_cache


def get_item_names() -> list[str]:
    return [item.get("name", "") for item in get_mapping() if item.get("name")]


def get_latest() -> dict[str, Any]:
    data = _get_json("latest")
    return data if isinstance(data, dict) else {}


def get_5m() -> dict[str, Any]:
    data = _get_json("5m")
    return data if isinstance(data, dict) else {}


def get_1h() -> dict[str, Any]:
    data = _get_json("1h")
    return data if isinstance(data, dict) else {}


def get_market_snapshot() -> dict[str, Any]:
    global _snapshot_cache, _snapshot_cache_bucket
    bucket = _current_bucket()
    if _snapshot_cache is not None and _snapshot_cache_bucket == bucket:
        return _snapshot_cache

    mapping = get_mapping()
    latest = get_latest()
    five_min = get_5m()
    one_hour = get_1h()

    latest_data = latest.get("data", {})
    five_min_data = five_min.get("data", {})
    one_hour_data = one_hour.get("data", {})

    items: list[dict[str, Any]] = []
    for item in mapping:
        item_id = item.get("id")
        if item_id is None:
            continue

        latest_price = latest_data.get(str(item_id), {})
        if not latest_price:
            continue

        high = latest_price.get("high")
        low = latest_price.get("low")
        if high is None or low is None:
            continue

        high = int(high or 0)
        low = int(low or 0)

        # Keep all usable items. Do not drop equal-price or temporarily inverted rows.
        if high <= 0 and low <= 0:
            continue

        if high <= 0 < low:
            high = low
        elif low <= 0 < high:
            low = high

        if high < low:
            high, low = low, high

        five_min_price = five_min_data.get(str(item_id), {})
        high_price_volume = int(five_min_price.get("highPriceVolume", 0) or 0)
        low_price_volume = int(five_min_price.get("lowPriceVolume", 0) or 0)
        recent_volume = high_price_volume + low_price_volume

        one_hour_price = one_hour_data.get(str(item_id), {})
        hour_high_price_volume = int(one_hour_price.get("highPriceVolume", 0) or 0)
        hour_low_price_volume = int(one_hour_price.get("lowPriceVolume", 0) or 0)
        hour_volume = hour_high_price_volume + hour_low_price_volume

        spread = max(high - low, 0)
        mid = (high + low) / 2
        spread_pct = (spread / low) * 100 if low else 0

        items.append(
            {
                "id": item_id,
                "name": item.get("name", f"Item {item_id}"),
                "limit": item.get("limit") or 0,
                "high": high,
                "low": low,
                "high_time": latest_price.get("highTime"),
                "low_time": latest_price.get("lowTime"),
                "spread": spread,
                "spread_pct": round(spread_pct, 3),
                "mid": round(mid, 2),
                "members": item.get("members", False),
                "high_price_volume": high_price_volume,
                "low_price_volume": low_price_volume,
                "recent_volume": recent_volume,
                "hour_high_price_volume": hour_high_price_volume,
                "hour_low_price_volume": hour_low_price_volume,
                "hour_volume": hour_volume,
            }
        )

    ensure_history_and_cache(items, snapshot_bucket=bucket)
    _snapshot_cache = {"items": items, "item_count": len(items), "snapshot_bucket": bucket}
    _snapshot_cache_bucket = bucket
    return _snapshot_cache