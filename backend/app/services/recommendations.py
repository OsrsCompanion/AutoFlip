from __future__ import annotations

import re
from typing import Any

from app.services.market_history import load_market_cache

GE_TAX_RATE = 0.02
GE_TAX_CAP = 5_000_000
MIN_VOLUME = 1000
DEFAULT_CATEGORY_SIZE = 10
ANCHOR_KEYWORDS = (
    "nature rune",
    "soul rune",
    "blood rune",
    "death rune",
    "chaos rune",
    "law rune",
    "cannonball",
    "battlestaff",
    "air orb",
    "dragon dart tip",
    "rune arrow",
)


def _tax(price: int) -> int:
    return min(int(price * GE_TAX_RATE), GE_TAX_CAP)


def _profit(buy: int, sell: int) -> int:
    return sell - _tax(sell) - buy


def _affordable_quantity(budget: int, price: int, limit: int) -> int:
    if budget <= 0 or price <= 0:
        return 0
    qty = budget // price
    if limit and limit > 0:
        qty = min(qty, limit)
    return qty


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _parse_budget(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value or "").strip().lower().replace(",", "")
    if not text:
        return 0

    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", text)
    if not match:
        return _to_int(value, 0)

    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1
    if suffix == "k":
        multiplier = 1_000
    elif suffix == "m":
        multiplier = 1_000_000
    elif suffix == "b":
        multiplier = 1_000_000_000

    return int(number * multiplier)


def _anchor_match(name: str) -> bool:
    normalized = name.strip().lower()
    return any(keyword in normalized for keyword in ANCHOR_KEYWORDS)


def _sort_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _to_float(item.get("roi_pct")),
            _to_int(item.get("potential_profit")),
            _to_int(item.get("recent_volume")),
        ),
        reverse=True,
    )[:limit]


def _candidate_from_cache_item(
    cache_item: dict[str, Any],
    allocation_budget: int,
    total_budget: int,
) -> dict[str, Any] | None:
    buy_price = _to_int(cache_item.get("buy_price"))
    sell_price = _to_int(cache_item.get("sell_price"))
    recent_volume = _to_int(cache_item.get("recent_volume"))
    buy_limit = _to_int(cache_item.get("buy_limit"))

    if buy_price <= 0 or sell_price <= 0 or sell_price <= buy_price:
        return None
    if recent_volume < MIN_VOLUME:
        return None
    if total_budget > 0 and buy_price > total_budget:
        return None

    profit_per_item = _profit(buy_price, sell_price)
    if profit_per_item <= 0:
        return None

    quantity = _affordable_quantity(allocation_budget, buy_price, buy_limit)
    if quantity <= 0:
        return None

    potential_profit = quantity * profit_per_item
    spread = sell_price - buy_price
    roi_pct = round((profit_per_item / buy_price) * 100, 3) if buy_price > 0 else 0.0

    day_low = _to_int(cache_item.get("day_low"), buy_price)
    day_high = _to_int(cache_item.get("day_high"), sell_price)
    week_low = _to_int(cache_item.get("week_low"), buy_price)
    week_high = _to_int(cache_item.get("week_high"), sell_price)
    month_low = _to_int(cache_item.get("month_low"), buy_price)
    month_high = _to_int(cache_item.get("month_high"), sell_price)

    item = {
        "id": _to_int(cache_item.get("id")),
        "name": str(cache_item.get("name") or "Unknown item"),
        "buy_price": buy_price,
        "sell_price": sell_price,
        "profit_per_item": profit_per_item,
        "recent_volume": recent_volume,
        "suggested_quantity": quantity,
        "capital_required": quantity * buy_price,
        "potential_profit": potential_profit,
        "spread": spread,
        "spread_pct": round((spread / buy_price) * 100, 3) if buy_price > 0 else 0.0,
        "roi_pct": roi_pct,
        "buy_limit": buy_limit,
        "day_low": day_low,
        "day_high": day_high,
        "week_low": week_low,
        "week_high": week_high,
        "month_low": month_low,
        "month_high": month_high,
        "avg_day_low": _to_float(cache_item.get("avg_day_low"), float(buy_price)),
        "avg_week_low": _to_float(cache_item.get("avg_week_low"), float(buy_price)),
        "avg_month_low": _to_float(cache_item.get("avg_month_low"), float(buy_price)),
        "dip_vs_day_pct": _to_float(cache_item.get("dip_vs_day_pct")),
        "dip_vs_week_pct": _to_float(cache_item.get("dip_vs_week_pct")),
        "dip_vs_month_pct": _to_float(cache_item.get("dip_vs_month_pct")),
        "stability_day_pct": _to_float(cache_item.get("stability_day_pct")),
        "stability_week_pct": _to_float(cache_item.get("stability_week_pct")),
        "stability_month_pct": _to_float(cache_item.get("stability_month_pct")),
        "history_points": _to_int(cache_item.get("history_points")),
        "updated_at": cache_item.get("updated_at"),
    }
    return item


def build_recommendations(
    settings: dict[str, Any],
    market_snapshot: dict[str, Any] | None = None,
    current_scan: dict[str, Any] | None = None,
    category_limit: int = DEFAULT_CATEGORY_SIZE,
) -> dict[str, Any]:
    del current_scan  # compatibility with ai_advisor baseline caller

    budget = _parse_budget(settings.get("budget", 0))
    available_slots = max(_to_int(settings.get("available_slots", 1), 1), 1)
    remaining_slots = available_slots
    per_slot_budget = budget // remaining_slots if budget > 0 and remaining_slots > 0 else budget
    allocation_budget = per_slot_budget if per_slot_budget > 0 else budget
    category_limit = max(1, min(50, _to_int(category_limit, DEFAULT_CATEGORY_SIZE)))

    cache = load_market_cache()
    cache_items = cache.get("items", []) if isinstance(cache, dict) else []

    candidate_pool: list[dict[str, Any]] = []
    for cache_item in cache_items:
        candidate = _candidate_from_cache_item(
            cache_item=cache_item,
            allocation_budget=allocation_budget,
            total_budget=budget,
        )
        if candidate is not None:
            candidate_pool.append(candidate)

    # Fallback only if cache is empty and a live snapshot was supplied.
    if not candidate_pool and isinstance(market_snapshot, dict):
        for raw_item in market_snapshot.get("items", []):
            low = _to_int(raw_item.get("low"))
            high = _to_int(raw_item.get("high"))
            recent_volume = _to_int(raw_item.get("recent_volume", raw_item.get("volume", 0)))
            buy_limit = _to_int(raw_item.get("limit"))
            if low <= 0 or high <= 0 or high <= low or recent_volume < MIN_VOLUME:
                continue
            if budget > 0 and low > budget:
                continue
            profit_per_item = _profit(low, high)
            if profit_per_item <= 0:
                continue
            quantity = _affordable_quantity(allocation_budget, low, buy_limit)
            if quantity <= 0:
                continue
            roi_pct = round((profit_per_item / low) * 100, 3) if low > 0 else 0.0
            candidate_pool.append(
                {
                    "id": _to_int(raw_item.get("id")),
                    "name": str(raw_item.get("name") or "Unknown item"),
                    "buy_price": low,
                    "sell_price": high,
                    "profit_per_item": profit_per_item,
                    "recent_volume": recent_volume,
                    "suggested_quantity": quantity,
                    "capital_required": quantity * low,
                    "potential_profit": quantity * profit_per_item,
                    "spread": high - low,
                    "spread_pct": round(((high - low) / low) * 100, 3) if low > 0 else 0.0,
                    "roi_pct": roi_pct,
                    "buy_limit": buy_limit,
                    "day_low": low,
                    "day_high": high,
                    "week_low": low,
                    "week_high": high,
                    "month_low": low,
                    "month_high": high,
                    "avg_day_low": float(low),
                    "avg_week_low": float(low),
                    "avg_month_low": float(low),
                    "dip_vs_day_pct": 0.0,
                    "dip_vs_week_pct": 0.0,
                    "dip_vs_month_pct": 0.0,
                    "stability_day_pct": 0.0,
                    "stability_week_pct": 0.0,
                    "stability_month_pct": 0.0,
                    "history_points": 0,
                    "updated_at": None,
                }
            )

    recommendations = _sort_items(candidate_pool, limit=min(20, max(category_limit, 10)))
    top_candidates = recommendations[:10]

    high_value_pool = [
        item
        for item in candidate_pool
        if item["spread_pct"] >= 2.0 or item["profit_per_item"] >= 1_000 or item["buy_price"] >= max(1, int(budget * 0.15))
    ]
    high_value = _sort_items(high_value_pool or candidate_pool, limit=category_limit)

    overnight_pool = [
        item
        for item in candidate_pool
        if item["dip_vs_week_pct"] >= 0.5 and item["stability_week_pct"] <= 12.0 and item["recent_volume"] >= MIN_VOLUME
    ]
    overnight = _sort_items(overnight_pool or candidate_pool, limit=category_limit)

    anchors_pool = [item for item in candidate_pool if _anchor_match(item["name"])]
    anchors = _sort_items(anchors_pool, limit=category_limit)

    dump_pool = [
        item
        for item in candidate_pool
        if item["dip_vs_day_pct"] >= 0.5 or item["dip_vs_week_pct"] >= 1.0
    ]
    dump = _sort_items(dump_pool or candidate_pool, limit=category_limit)

    return {
        "recommendations": recommendations,
        "top_candidates": top_candidates,
        "high_value": high_value,
        "overnight": overnight,
        "anchors": anchors,
        "dump": dump,
        "remaining_slots": remaining_slots,
        "per_slot_budget": per_slot_budget,
        "snapshot_bucket": cache.get("snapshot_bucket") if isinstance(cache, dict) else None,
        "cache_updated_at": cache.get("updated_at") if isinstance(cache, dict) else None,
        "cache_item_count": len(cache_items),
        "candidate_count": len(candidate_pool),
    }
