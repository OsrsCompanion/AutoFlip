from __future__ import annotations

import re
from typing import Any

from app.services.market_history import get_item_history, load_market_cache


def _to_int(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _safe_pct_change(start_value: float, end_value: float) -> float:
    if start_value <= 0:
        return 0.0
    return round(((end_value - start_value) / start_value) * 100, 3)


def _avg(values: list[float]) -> float:
    vals = [float(v) for v in values if float(v) > 0]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _segment_average(values: list[float], start_ratio: float, end_ratio: float) -> float:
    if not values:
        return 0.0
    start = int(len(values) * start_ratio)
    end = max(start + 1, int(len(values) * end_ratio))
    return _avg(values[start:end])


def _normalize_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _base_name(value: Any) -> str:
    text = _normalize_name(value)
    return re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()


def _canonical_tokens(value: Any) -> list[str]:
    text = re.sub(r"[^a-z0-9]+", " ", _base_name(value))
    return [token for token in text.split() if token]


def _name_match_score(query: str, candidate_name: str) -> int:
    query_norm = _normalize_name(query)
    candidate_norm = _normalize_name(candidate_name)
    query_base = _base_name(query)
    candidate_base = _base_name(candidate_name)

    if not query_norm:
        return -1

    score = 0
    if candidate_norm == query_norm:
        score += 10000
    if candidate_base == query_norm:
        score += 9500
    if candidate_base == query_base:
        score += 9000
    if candidate_norm.startswith(query_norm):
        score += 4000
    if candidate_base.startswith(query_base):
        score += 3500
    if query_norm in candidate_norm:
        score += 1500
    if query_base and query_base in candidate_base:
        score += 1200

    query_tokens = _canonical_tokens(query)
    candidate_tokens = _canonical_tokens(candidate_name)
    if query_tokens and candidate_tokens:
        overlap = len(set(query_tokens) & set(candidate_tokens))
        score += overlap * 250
        if overlap == len(query_tokens):
            score += 900

    if candidate_norm != candidate_base:
        score -= 75

    return score


def _trend_from_points(points: list[dict[str, Any]], label: str) -> dict[str, Any]:
    lows = [_to_float(point.get("low")) for point in points if _to_float(point.get("low")) > 0]
    if len(lows) < 4:
        return {
            f"{label}_trend": "unknown",
            f"{label}_trend_pct": 0.0,
            f"{label}_latest_low": _to_int(lows[-1]) if lows else 0,
            f"{label}_start_avg": 0,
            f"{label}_end_avg": 0,
            f"{label}_points": len(lows),
        }

    start_avg = _segment_average(lows, 0.0, 0.33)
    end_avg = _segment_average(lows, 0.67, 1.0)
    change_pct = _safe_pct_change(start_avg, end_avg)

    if change_pct <= -4:
        trend = "down_hard"
    elif change_pct <= -1.5:
        trend = "down"
    elif change_pct >= 4:
        trend = "up_hard"
    elif change_pct >= 1.5:
        trend = "up"
    else:
        trend = "flat"

    return {
        f"{label}_trend": trend,
        f"{label}_trend_pct": change_pct,
        f"{label}_latest_low": _to_int(lows[-1]),
        f"{label}_start_avg": _to_int(start_avg),
        f"{label}_end_avg": _to_int(end_avg),
        f"{label}_points": len(lows),
    }


def _range_position_pct(current_value: int, low_value: int, high_value: int) -> float:
    if low_value <= 0 or high_value <= low_value:
        return 0.0
    return round(((current_value - low_value) / (high_value - low_value)) * 100, 3)


def _confidence_for_window(window: str, points: int) -> dict[str, Any]:
    window = str(window or "").lower()
    points = _to_int(points)
    threshold_map = {
        "last_4h": (12, 24, 48),
        "day": (24, 72, 180),
        "week": (48, 144, 288),
        "month": (96, 288, 576),
    }
    low_threshold, medium_threshold, high_threshold = threshold_map.get(window, (24, 72, 180))

    if points < low_threshold:
        level = "very_low"
        summary = f"Only {points} points. Too thin for a reliable {window} read."
    elif points < medium_threshold:
        level = "low"
        summary = f"{points} points. Early {window} read only; use cautious wording."
    elif points < high_threshold:
        level = "medium"
        summary = f"{points} points. Usable for {window} guidance, but not strongly predictive."
    else:
        level = "high"
        summary = f"{points} points. Stronger {window} coverage."

    return {
        "window": window,
        "points": points,
        "confidence_level": level,
        "confidence_summary": summary,
    }


def _question_time_horizon(user_query: str) -> dict[str, Any]:
    text = _normalize_name(user_query)
    if not text:
        return {"requested_horizon": "general", "preferred_window": "day", "needs_longer_history": False}

    short_markers = [
        "right now", "now", "today", "tonight", "next few hours", "next hour",
        "4 hours", "four hours", "short term", "intraday", "this flip",
    ]
    long_markers = [
        "few days", "couple days", "tomorrow", "this week", "next week", "long term",
        "longer term", "crashing", "crash", "trend", "hold off", "another day",
        "another two days", "for days", "over the week", "month", "weekly",
    ]

    if any(marker in text for marker in long_markers):
        return {"requested_horizon": "longer_term", "preferred_window": "week", "needs_longer_history": True}
    if any(marker in text for marker in short_markers):
        return {"requested_horizon": "short_term", "preferred_window": "last_4h", "needs_longer_history": False}
    return {"requested_horizon": "general", "preferred_window": "day", "needs_longer_history": False}


def _answerability_summary(question_profile: dict[str, Any], confidence: dict[str, Any]) -> dict[str, Any]:
    requested_horizon = question_profile.get("requested_horizon", "general")
    preferred_window = question_profile.get("preferred_window", "day")
    level = confidence.get("confidence_level", "very_low")

    if requested_horizon == "short_term":
        if level in {"medium", "high"}:
            verdict = "good_for_short_term"
            summary = "Enough recent data for a short-term answer."
        elif level == "low":
            verdict = "cautious_short_term"
            summary = "Some recent data exists, but the short-term answer should stay cautious."
        else:
            verdict = "insufficient_short_term"
            summary = "Not enough recent data for a trustworthy short-term answer."
    elif requested_horizon == "longer_term":
        if level == "high":
            verdict = "usable_for_longer_term"
            summary = "Enough broader history exists for a more confident longer-term answer."
        elif level == "medium":
            verdict = "limited_for_longer_term"
            summary = "Some broader history exists, but longer-term conclusions should stay conservative."
        else:
            verdict = "insufficient_for_longer_term"
            summary = "Not enough broader history for a trustworthy longer-term answer."
    else:
        if level in {"medium", "high"}:
            verdict = "usable_general"
            summary = "Enough data exists for a practical answer."
        else:
            verdict = "limited_general"
            summary = "Only limited data exists, so the answer should stay conservative."

    return {
        "preferred_window": preferred_window,
        "requested_horizon": requested_horizon,
        "answerability_verdict": verdict,
        "answerability_summary": summary,
    }


def _crash_risk(day_trend: str, week_trend: str, month_trend: str, month_position_pct: float) -> str:
    if day_trend in {"down_hard", "down"} and week_trend in {"down_hard", "down"}:
        if month_position_pct <= 20:
            return "high"
        return "medium"
    if day_trend in {"down_hard", "down"} or week_trend in {"down_hard", "down"}:
        return "medium"
    if month_trend in {"down_hard", "down"}:
        return "medium"
    return "low"


def _entry_signal(
    day_trend: str,
    week_trend: str,
    crash_risk: str,
    week_position_pct: float,
    month_position_pct: float,
) -> str:
    if crash_risk == "high":
        return "hold_off"
    if day_trend in {"down_hard", "down"} and week_trend in {"down_hard", "down"}:
        return "hold_off"
    if day_trend in {"flat", "up", "up_hard"} and week_position_pct <= 25 and month_position_pct <= 35:
        return "watch_for_reversal"
    if day_trend in {"up", "up_hard"} and week_trend in {"flat", "up", "up_hard"}:
        return "momentum_positive"
    return "neutral"


def _advisor_hint(entry_signal: str, crash_risk: str) -> str:
    if entry_signal == "hold_off" and crash_risk == "high":
        return "Hold off for now. The item is still trending down across recent history and looks weak."
    if entry_signal == "hold_off":
        return "Hold off for now. Recent history is still soft and the trend has not stabilized yet."
    if entry_signal == "watch_for_reversal":
        return "Watch it closely. Price is near the lower end of its range, but wait for stabilization before buying."
    if entry_signal == "momentum_positive":
        return "Momentum is improving. A careful entry is more reasonable if spread and volume still look healthy."
    return "Mixed signal. Use spread, volume, and your time horizon before entering."


def _find_offer_for_item(item_name: str, current_scan: dict[str, Any] | None) -> dict[str, Any] | None:
    offers = (current_scan or {}).get("offers") or []
    target = _normalize_name(item_name)
    target_base = _base_name(item_name)
    for offer in offers:
        offer_name = str(offer.get("item_name") or "")
        if _normalize_name(offer_name) == target or _base_name(offer_name) == target_base:
            return offer
    return None


def _cache_item_by_id(item_id: int) -> dict[str, Any] | None:
    cache = load_market_cache()
    for item in cache.get("items", []) if isinstance(cache, dict) else []:
        if _to_int(item.get("id")) == item_id:
            return item
    return None


def _cache_item_by_query(query: str) -> dict[str, Any] | None:
    text = str(query or "").strip()
    if not text:
        return None

    cache = load_market_cache()
    items = cache.get("items", []) if isinstance(cache, dict) else []
    if not items:
        return None

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for item in items:
        name = str(item.get("name") or "")
        score = _name_match_score(text, name)
        if score <= 0:
            continue
        volume = _to_int(item.get("recent_volume"))
        profit = _to_int(item.get("profit_per_item"))
        scored.append((score, volume + profit, item))

    if not scored:
        return None

    scored.sort(key=lambda row: (row[0], row[1], str(row[2].get("name") or "").lower()), reverse=True)
    return scored[0][2]


def build_ai_item_context(
    item_id: int,
    current_scan: dict[str, Any] | None = None,
    user_query: str = "",
) -> dict[str, Any]:
    item = _cache_item_by_id(item_id)
    if not item:
        return {
            "found": False,
            "item_id": item_id,
            "error": "Item not found in market cache.",
        }

    day_points = get_item_history(item_id=item_id, window="day")
    week_points = get_item_history(item_id=item_id, window="week")
    month_points = get_item_history(item_id=item_id, window="month")
    last_4h_points = day_points[-48:] if len(day_points) > 48 else day_points

    day_trend = _trend_from_points(day_points, "day")
    week_trend = _trend_from_points(week_points, "week")
    month_trend = _trend_from_points(month_points, "month")
    last_4h_trend = _trend_from_points(last_4h_points, "last_4h")

    buy_price = _to_int(item.get("buy_price"))
    week_position_pct = _range_position_pct(buy_price, _to_int(item.get("week_low")), _to_int(item.get("week_high")))
    month_position_pct = _range_position_pct(buy_price, _to_int(item.get("month_low")), _to_int(item.get("month_high")))
    crash_risk = _crash_risk(
        day_trend.get("day_trend", "unknown"),
        week_trend.get("week_trend", "unknown"),
        month_trend.get("month_trend", "unknown"),
        month_position_pct,
    )
    entry_signal = _entry_signal(
        day_trend.get("day_trend", "unknown"),
        week_trend.get("week_trend", "unknown"),
        crash_risk,
        week_position_pct,
        month_position_pct,
    )

    question_profile = _question_time_horizon(user_query)
    confidence = {
        "last_4h": _confidence_for_window("last_4h", last_4h_trend.get("last_4h_points", 0)),
        "day": _confidence_for_window("day", day_trend.get("day_points", 0)),
        "week": _confidence_for_window("week", week_trend.get("week_points", 0)),
        "month": _confidence_for_window("month", month_trend.get("month_points", 0)),
    }
    preferred_window = str(question_profile.get("preferred_window", "day"))
    answerability = _answerability_summary(question_profile, confidence.get(preferred_window, confidence["day"]))

    current_offer = _find_offer_for_item(str(item.get("name") or ""), current_scan)

    return {
        "found": True,
        "item_id": _to_int(item.get("id")),
        "item_name": str(item.get("name") or f"Item {item_id}"),
        "matched_query_name": str(item.get("name") or f"Item {item_id}"),
        "question_profile": question_profile,
        "answerability": answerability,
        "confidence": confidence,
        "current_market": {
            "buy_price": buy_price,
            "sell_price": _to_int(item.get("sell_price")),
            "spread": _to_int(item.get("spread")),
            "profit_per_item": _to_int(item.get("profit_per_item")),
            "roi_pct": round(_to_float(item.get("roi_pct")), 3),
            "recent_volume_1h": _to_int(item.get("recent_volume")),
            "avg_daily_volume": _to_int(item.get("avg_daily_volume")),
            "buy_limit": _to_int(item.get("buy_limit")),
        },
        "ranges": {
            "day_low": _to_int(item.get("day_low")),
            "day_high": _to_int(item.get("day_high")),
            "week_low": _to_int(item.get("week_low")),
            "week_high": _to_int(item.get("week_high")),
            "month_low": _to_int(item.get("month_low")),
            "month_high": _to_int(item.get("month_high")),
            "week_position_pct": week_position_pct,
            "month_position_pct": month_position_pct,
        },
        "signals": {
            **last_4h_trend,
            **day_trend,
            **week_trend,
            **month_trend,
            "crash_risk": crash_risk,
            "entry_signal": entry_signal,
            "advisor_hint": _advisor_hint(entry_signal, crash_risk),
        },
        "cache_metrics": {
            "dip_vs_day_pct": round(_to_float(item.get("dip_vs_day_pct")), 3),
            "dip_vs_week_pct": round(_to_float(item.get("dip_vs_week_pct")), 3),
            "dip_vs_month_pct": round(_to_float(item.get("dip_vs_month_pct")), 3),
            "stability_day_pct": round(_to_float(item.get("stability_day_pct")), 3),
            "stability_week_pct": round(_to_float(item.get("stability_week_pct")), 3),
            "stability_month_pct": round(_to_float(item.get("stability_month_pct")), 3),
            "history_points": _to_int(item.get("history_points")),
            "updated_at": item.get("updated_at"),
        },
        "current_offer": current_offer,
    }


def build_ai_context_for_query(query: str, current_scan: dict[str, Any] | None = None) -> dict[str, Any]:
    item = _cache_item_by_query(query)
    if not item:
        return {
            "found": False,
            "query": query,
            "error": "No matching item found in market cache.",
        }
    payload = build_ai_item_context(item_id=_to_int(item.get("id")), current_scan=current_scan, user_query=query)
    if payload.get("found"):
        payload["query"] = query
        payload["matched_query_name"] = str(item.get("name") or "")
    return payload
