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


def _candidate_from_cache_item(cache_item: dict[str, Any], allocation_budget: int, total_budget: int) -> dict[str, Any] | None:
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

    spread = sell_price - buy_price
    roi_pct = round((profit_per_item / buy_price) * 100, 3) if buy_price > 0 else 0.0
    return {
        "id": _to_int(cache_item.get("id")),
        "name": str(cache_item.get("name") or "Unknown item"),
        "buy_price": buy_price,
        "sell_price": sell_price,
        "profit_per_item": profit_per_item,
        "recent_volume": recent_volume,
        "suggested_quantity": quantity,
        "capital_required": quantity * buy_price,
        "potential_profit": quantity * profit_per_item,
        "spread": spread,
        "spread_pct": round((spread / buy_price) * 100, 3) if buy_price > 0 else 0.0,
        "roi_pct": roi_pct,
        "buy_limit": buy_limit,
        "day_low": _to_int(cache_item.get("day_low"), buy_price),
        "day_high": _to_int(cache_item.get("day_high"), sell_price),
        "week_low": _to_int(cache_item.get("week_low"), buy_price),
        "week_high": _to_int(cache_item.get("week_high"), sell_price),
        "month_low": _to_int(cache_item.get("month_low"), buy_price),
        "month_high": _to_int(cache_item.get("month_high"), sell_price),
        "dip_vs_day_pct": _to_float(cache_item.get("dip_vs_day_pct")),
        "dip_vs_week_pct": _to_float(cache_item.get("dip_vs_week_pct")),
        "dip_vs_month_pct": _to_float(cache_item.get("dip_vs_month_pct")),
        "stability_day_pct": _to_float(cache_item.get("stability_day_pct")),
        "stability_week_pct": _to_float(cache_item.get("stability_week_pct")),
        "stability_month_pct": _to_float(cache_item.get("stability_month_pct")),
        "history_points": _to_int(cache_item.get("history_points")),
        "updated_at": cache_item.get("updated_at"),
    }


def _snapshot_candidates(market_snapshot: dict[str, Any], allocation_budget: int, total_budget: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(market_snapshot, dict):
        return out
    for raw_item in market_snapshot.get("items", []):
        low = _to_int(raw_item.get("low", raw_item.get("buy_price")))
        high = _to_int(raw_item.get("high", raw_item.get("sell_price")))
        recent_volume = _to_int(raw_item.get("recent_volume", raw_item.get("volume", 0)))
        buy_limit = _to_int(raw_item.get("limit", raw_item.get("buy_limit")))
        if low <= 0 or high <= 0 or high <= low or recent_volume < MIN_VOLUME:
            continue
        if total_budget > 0 and low > total_budget:
            continue
        profit_per_item = _profit(low, high)
        if profit_per_item <= 0:
            continue
        quantity = _affordable_quantity(allocation_budget, low, buy_limit)
        if quantity <= 0:
            continue
        roi_pct = round((profit_per_item / low) * 100, 3) if low > 0 else 0.0
        out.append(
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
    return out


def _web_safe(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "buy_price": item.get("buy_price"),
        "sell_price": item.get("sell_price"),
        "profit_per_item": item.get("profit_per_item"),
        "recent_volume": item.get("recent_volume"),
        "spread": item.get("spread"),
        "spread_pct": item.get("spread_pct"),
        "roi_pct": item.get("roi_pct"),
        "reason_tags": [
            "high-roi" if _to_float(item.get("roi_pct")) >= 2.0 else "steady",
            "liquid" if _to_int(item.get("recent_volume")) >= 5000 else "selective",
        ],
    }


def _plugin_full(item: dict[str, Any], slot_index: int) -> dict[str, Any]:
    row = dict(item)
    row["slot_index"] = slot_index
    row["action"] = "buy_then_list"
    row["target_profit"] = row.get("potential_profit", 0)
    return row


def build_recommendations(
    settings: dict[str, Any],
    market_snapshot: dict[str, Any] | None = None,
    current_scan: dict[str, Any] | None = None,
    category_limit: int = DEFAULT_CATEGORY_SIZE,
    mode: str = "web_safe",
) -> dict[str, Any]:
    del current_scan
    budget = _parse_budget(settings.get("budget", 0))
    available_slots = max(_to_int(settings.get("available_slots", settings.get("slots_available", 1)), 1), 1)
    remaining_slots = available_slots
    per_slot_budget = budget // remaining_slots if budget > 0 and remaining_slots > 0 else budget
    allocation_budget = per_slot_budget if per_slot_budget > 0 else budget
    category_limit = max(1, min(50, _to_int(category_limit, DEFAULT_CATEGORY_SIZE)))

    cache = load_market_cache()
    cache_items = cache.get("items", []) if isinstance(cache, dict) else []
    candidate_pool = [
        candidate
        for cache_item in cache_items
        if (candidate := _candidate_from_cache_item(cache_item, allocation_budget, budget)) is not None
    ]
    if not candidate_pool and market_snapshot:
        candidate_pool = _snapshot_candidates(market_snapshot, allocation_budget, budget)

    top_all = _sort_items(candidate_pool, limit=min(40, max(category_limit * 2, 10)))
    recommendations = top_all[:category_limit]
    high_value = _sort_items(
        [i for i in candidate_pool if i["spread_pct"] >= 2.0 or i["profit_per_item"] >= 1_000],
        category_limit,
    ) or recommendations
    overnight = _sort_items(
        [i for i in candidate_pool if i["dip_vs_week_pct"] >= 0.5 and i["stability_week_pct"] <= 12.0],
        category_limit,
    ) or recommendations
    anchors = _sort_items([i for i in candidate_pool if _anchor_match(i["name"])], category_limit)
    dump = _sort_items([i for i in candidate_pool if i["dip_vs_day_pct"] >= 0.5 or i["dip_vs_week_pct"] >= 1.0], category_limit) or recommendations

    if mode == "plugin_full":
        mapper = _plugin_full
        mapped_recommendations = [mapper(item, idx + 1) for idx, item in enumerate(recommendations)]
        mapped_top = [mapper(item, idx + 1) for idx, item in enumerate(top_all[: min(available_slots, len(top_all))])]
        return {
            "mode": mode,
            "recommendations": mapped_recommendations,
            "top_candidates": mapped_top,
            "high_value": [mapper(item, idx + 1) for idx, item in enumerate(high_value)],
            "overnight": [mapper(item, idx + 1) for idx, item in enumerate(overnight)],
            "anchors": [mapper(item, idx + 1) for idx, item in enumerate(anchors)],
            "dump": [mapper(item, idx + 1) for idx, item in enumerate(dump)],
            "remaining_slots": remaining_slots,
            "per_slot_budget": per_slot_budget,
            "snapshot_bucket": cache.get("snapshot_bucket") if isinstance(cache, dict) else None,
            "cache_updated_at": cache.get("updated_at") if isinstance(cache, dict) else None,
            "candidate_count": len(candidate_pool),
        }

    preview_limit = min(category_limit, 5)
    return {
        "mode": "web_safe",
        "recommendations": [_web_safe(item) for item in recommendations[:preview_limit]],
        "top_candidates": [_web_safe(item) for item in top_all[: min(3, len(top_all))]],
        "high_value": [_web_safe(item) for item in high_value[:preview_limit]],
        "overnight": [_web_safe(item) for item in overnight[:preview_limit]],
        "anchors": [_web_safe(item) for item in anchors[:preview_limit]],
        "dump": [_web_safe(item) for item in dump[:preview_limit]],
        "remaining_slots": remaining_slots,
        "per_slot_budget": per_slot_budget,
        "snapshot_bucket": cache.get("snapshot_bucket") if isinstance(cache, dict) else None,
        "cache_updated_at": cache.get("updated_at") if isinstance(cache, dict) else None,
        "candidate_count": len(candidate_pool),
    }
