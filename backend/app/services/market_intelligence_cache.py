from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from app.services import market_history as mh

GE_TAX_RATE = 0.02
GE_TAX_CAP = 5_000_000
MAX_BACKFILL_DAYS = 370
DEFAULT_BACKFILL_DAYS = 105

DERIVED_MARKET_CACHE_PATH = str((Path(mh.CACHE_DIR) / "derived_market_cache.json").resolve())
RECOMMENDATION_CANDIDATE_CACHE_PATH = str((Path(mh.CACHE_DIR) / "recommendation_candidate_cache.json").resolve())
MARKET_INTELLIGENCE_STATE_PATH = str((Path(mh.CACHE_DIR) / "market_intelligence_state.json").resolve())
MARKET_INTELLIGENCE_WINDOWS_PATH = str((Path(mh.CACHE_DIR) / "market_intelligence_windows.json").resolve())

WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "3_5d": timedelta(hours=84),
    "10d": timedelta(days=10),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "105d": timedelta(days=105),
}

LONG_WINDOW_NAMES: tuple[str, ...] = ("10d", "30d", "90d", "105d")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _window_metric_is_valid(metric: Any) -> bool:
    if not isinstance(metric, dict):
        return False
    sample_count = _to_int(metric.get("sample_count"))
    if sample_count <= 0:
        return True
    oldest = _parse_ts(metric.get("oldest_ts"))
    newest = _parse_ts(metric.get("newest_ts"))
    if oldest is None or newest is None:
        return False
    return oldest <= newest


def _sanitize_windows(windows: Any) -> dict[str, Any]:
    if not isinstance(windows, dict):
        return {}
    clean: dict[str, Any] = {}
    for name, metric in windows.items():
        if name not in WINDOWS:
            continue
        if _window_metric_is_valid(metric):
            clean[name] = metric
    return clean


def _count_invalid_windows(items: Any, window_names: Iterable[str] | None = None) -> int:
    if not isinstance(items, dict):
        return 0
    names = set(window_names or WINDOWS.keys())
    invalid = 0
    for entry in items.values():
        windows = entry.get("windows", {}) if isinstance(entry, dict) else {}
        if not isinstance(windows, dict):
            continue
        for name in names:
            metric = windows.get(name)
            if metric is not None and not _window_metric_is_valid(metric):
                invalid += 1
    return invalid


def _tax(price: int) -> int:
    return min(int(price * GE_TAX_RATE), GE_TAX_CAP)


def _profit_after_tax(buy_price: int, sell_price: int) -> int:
    if buy_price <= 0 or sell_price <= 0:
        return 0
    return max(sell_price - _tax(sell_price) - buy_price, 0)


def _roi_pct(buy_price: int, sell_price: int) -> float:
    profit = _profit_after_tax(buy_price, sell_price)
    return round((profit / buy_price) * 100, 6) if buy_price > 0 else 0.0


def _safe_pct(base: float, current: float) -> float:
    if base <= 0:
        return 0.0
    return round(((current - base) / base) * 100.0, 6)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def _skewness(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    avg = _mean(values)
    sd = _stddev(values)
    if sd <= 0:
        return 0.0
    return sum(((value - avg) / sd) ** 3 for value in values) / len(values)


def _kurtosis(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    avg = _mean(values)
    sd = _stddev(values)
    if sd <= 0:
        return 0.0
    return (sum(((value - avg) / sd) ** 4 for value in values) / len(values)) - 3.0


def _linear_slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x_avg = (n - 1) / 2.0
    y_avg = _mean(values)
    denom = sum((idx - x_avg) ** 2 for idx in range(n))
    if denom <= 0:
        return 0.0
    return sum((idx - x_avg) * (value - y_avg) for idx, value in enumerate(values)) / denom


def _autocorrelation_lag1(values: list[float]) -> float:
    if len(values) < 3:
        return 0.0
    left = values[:-1]
    right = values[1:]
    left_avg = _mean(left)
    right_avg = _mean(right)
    denom_left = math.sqrt(sum((v - left_avg) ** 2 for v in left))
    denom_right = math.sqrt(sum((v - right_avg) ** 2 for v in right))
    denom = denom_left * denom_right
    if denom <= 0:
        return 0.0
    return sum((a - left_avg) * (b - right_avg) for a, b in zip(left, right)) / denom


def _entropy(values: list[float], buckets: int = 10) -> float:
    if len(values) < 2:
        return 0.0
    low = min(values)
    high = max(values)
    if high <= low:
        return 0.0
    counts = [0] * buckets
    for value in values:
        idx = min(buckets - 1, int(((value - low) / (high - low)) * buckets))
        counts[idx] += 1
    total = len(values)
    raw = 0.0
    for count in counts:
        if count <= 0:
            continue
        p = count / total
        raw -= p * math.log(p, 2)
    return raw / math.log(buckets, 2)


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak)
    return worst * 100.0


def _window_metrics(samples: list[dict[str, Any]], newest_ts: datetime | None) -> dict[str, Any]:
    if not samples:
        return {"sample_count": 0, "quality": "missing"}
    samples = sorted(samples, key=lambda sample: _parse_ts(sample.get("ts")) or datetime.min.replace(tzinfo=UTC))
    lows = [_to_float(s.get("low")) for s in samples if _to_float(s.get("low")) > 0]
    highs = [_to_float(s.get("high")) for s in samples if _to_float(s.get("high")) > 0]
    spreads = [_to_float(s.get("spread")) for s in samples]
    rois = [_to_float(s.get("roi")) for s in samples]
    volumes = [_to_float(s.get("volume")) for s in samples]
    mid_prices = [((lo + hi) / 2.0) for lo, hi in zip(lows, highs) if lo > 0 and hi > 0]
    roi_mean = _mean(rois)
    roi_std = _stddev(rois)
    spread_mean = _mean(spreads)
    spread_std = _stddev(spreads)
    zscores = [abs((value - roi_mean) / roi_std) for value in rois] if roi_std > 0 else []
    spike_count = sum(1 for z in zscores if z >= 2.0)
    extreme_spike_count = sum(1 for z in zscores if z >= 3.0)
    positive_spreads = sum(1 for value in spreads if value > 0)
    first_price = mid_prices[0] if mid_prices else 0.0
    last_price = mid_prices[-1] if mid_prices else 0.0
    first_spread = spreads[0] if spreads else 0.0
    last_spread = spreads[-1] if spreads else 0.0
    sample_count = len(samples)
    oldest = samples[0].get("ts")
    newest = samples[-1].get("ts")
    quality = "good" if sample_count >= 20 else "thin" if sample_count >= 5 else "weak"
    return {
        "sample_count": sample_count,
        "oldest_ts": oldest,
        "newest_ts": newest,
        "quality": quality,
        "roi_avg": round(roi_mean, 6),
        "roi_median": round(float(median(rois)), 6) if rois else 0.0,
        "roi_min": round(min(rois), 6) if rois else 0.0,
        "roi_max": round(max(rois), 6) if rois else 0.0,
        "roi_stddev": round(roi_std, 6),
        "roi_variance": round(roi_std ** 2, 6),
        "roi_skewness": round(_skewness(rois), 6),
        "roi_kurtosis": round(_kurtosis(rois), 6),
        "spread_avg": round(spread_mean, 6),
        "spread_median": round(float(median(spreads)), 6) if spreads else 0.0,
        "spread_min": round(min(spreads), 6) if spreads else 0.0,
        "spread_max": round(max(spreads), 6) if spreads else 0.0,
        "spread_stddev": round(spread_std, 6),
        "spread_positive_ratio": round(positive_spreads / sample_count, 6) if sample_count else 0.0,
        "volume_avg": round(_mean(volumes), 6),
        "volume_stddev": round(_stddev(volumes), 6),
        "volume_total": int(sum(volumes)),
        "price_trend_pct": _safe_pct(first_price, last_price),
        "spread_trend_pct": _safe_pct(first_spread, last_spread),
        "price_slope": round(_linear_slope(mid_prices), 6),
        "spread_slope": round(_linear_slope(spreads), 6),
        "trend_strength": round(min(1.0, abs(_linear_slope(mid_prices)) / max(_mean(mid_prices), 1.0)), 6) if mid_prices else 0.0,
        "spike_count": spike_count,
        "extreme_spike_count": extreme_spike_count,
        "spike_ratio": round(spike_count / sample_count, 6) if sample_count else 0.0,
        "outlier_zscore_max": round(max(zscores), 6) if zscores else 0.0,
        "price_entropy": round(_entropy(mid_prices), 6),
        "spread_entropy": round(_entropy(spreads), 6),
        "autocorrelation_lag1": round(_autocorrelation_lag1(mid_prices), 6),
        "max_drawdown_pct": round(_max_drawdown(mid_prices), 6),
    }


def _row_from_market_item(item: dict[str, Any], state_by_id: dict[str, Any] | None = None) -> dict[str, Any] | None:
    item_id = _to_int(item.get("id") or item.get("item_id"))
    buy_price = _to_int(item.get("buy_price") or item.get("buy"))
    sell_price = _to_int(item.get("sell_price") or item.get("sell"))
    if item_id <= 0 or buy_price <= 0 or sell_price <= 0:
        return None
    state = (state_by_id or {}).get(str(item_id), {})
    windows = _sanitize_windows(state.get("windows", {}) if isinstance(state, dict) else {})
    profit_after_tax = _profit_after_tax(buy_price, sell_price)
    spread_gp = max(sell_price - buy_price, 0)
    spread_pct = round((spread_gp / buy_price) * 100, 6) if buy_price > 0 else 0.0
    roi_pct_after_tax = _roi_pct(buy_price, sell_price)
    snapshot_volume_5m = _to_int(item.get("snapshot_volume_5m") or item.get("recent_volume"))
    volume_1h = _to_int(item.get("recent_volume") or item.get("volume_1h"))
    day_volume = _to_int(item.get("day_volume"))
    week_volume = _to_int(item.get("week_volume"))
    month_volume = _to_int(item.get("month_volume"))
    risk_window = windows.get("10d") or windows.get("3_5d") or {}
    roi_std = _to_float(risk_window.get("roi_stddev"))
    spike_ratio = _to_float(risk_window.get("spike_ratio"))
    spread_positive_ratio = _to_float(risk_window.get("spread_positive_ratio"), 1.0)
    stability_score = round(max(0.0, min(100.0, 100.0 - (roi_std * 5.0) - (spike_ratio * 100.0) - ((1.0 - spread_positive_ratio) * 50.0))), 6)
    fill_speed_band = "fast" if volume_1h >= 50_000 or snapshot_volume_5m >= 5_000 else "medium" if volume_1h >= 10_000 or snapshot_volume_5m >= 1_000 else "slow" if volume_1h >= 1_000 or snapshot_volume_5m >= 100 else "thin"
    confidence_band = "high" if stability_score >= 80 and volume_1h >= 10_000 else "medium" if stability_score >= 60 and volume_1h >= 1_000 else "weak"
    buy_limit = _to_int(item.get("buy_limit"))
    liquidity_cap = max(1, int(volume_1h * 0.10)) if volume_1h > 0 else 0
    qty_cap = min([cap for cap in (buy_limit, liquidity_cap) if cap > 0], default=0)
    max_deployable_gp = qty_cap * buy_price
    return {
        "id": item_id,
        "item_id": item_id,
        "name": str(item.get("name") or item.get("item_name") or f"Item {item_id}"),
        "item_name": str(item.get("name") or item.get("item_name") or f"Item {item_id}"),
        "buy": buy_price,
        "sell": sell_price,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "profit_after_tax": profit_after_tax,
        "profit_after_tax_per_item": profit_after_tax,
        "roi_pct_after_tax": roi_pct_after_tax,
        "snapshot_volume_5m": snapshot_volume_5m,
        "volume_1h": volume_1h,
        "day_volume": day_volume,
        "week_volume": week_volume,
        "month_volume": month_volume,
        "avg_daily_volume": _to_int(item.get("avg_daily_volume")),
        "buy_limit": buy_limit,
        "high_alch_value": _to_int(item.get("high_alch_value") or item.get("high_alch")),
        "freshness_ts": item.get("updated_at"),
        "last_snapshot_ts": item.get("updated_at"),
        "spread_gp": spread_gp,
        "spread_pct": spread_pct,
        "volatility_1h": _to_float((windows.get("1h") or {}).get("roi_stddev")),
        "volatility_24h": _to_float((windows.get("24h") or {}).get("roi_stddev")),
        "stability_score": stability_score,
        "fill_speed_band": fill_speed_band,
        "max_deployable_gp": max_deployable_gp,
        "confidence_band": confidence_band,
        "market_proxy_fill_probability": None,
        "fill_model_source": "public_market_proxy_pending",
        "empirical_fill_rate": None,
        "telemetry_sample_count": 0,
        "windows": windows,
        "risk_evidence": _risk_evidence(windows),
        "source_market_cache_updated_at": item.get("updated_at"),
    }


def _risk_evidence(windows: dict[str, Any]) -> dict[str, Any]:
    w10 = windows.get("10d") or {}
    w90 = windows.get("90d") or {}
    z10 = _to_float(w10.get("outlier_zscore_max"))
    spike10 = _to_int(w10.get("spike_count"))
    spike90 = _to_int(w90.get("spike_count"))
    roi_std10 = _to_float(w10.get("roi_stddev"))
    spread_persistence = _to_float(w10.get("spread_positive_ratio"))
    trend = _to_float(w10.get("price_trend_pct"))
    trend_label = "stable"
    if trend >= 5:
        trend_label = "up"
    elif trend <= -5:
        trend_label = "down"
    return {
        "too_spiky": spike10 >= 10 or z10 >= 4.0,
        "spike_count_10d": spike10,
        "spike_count_90d": spike90,
        "max_zscore_10d": z10,
        "roi_stddev_10d": roi_std10,
        "spread_persistence_10d": spread_persistence,
        "trend_label": trend_label,
    }


def _load_state() -> dict[str, Any]:
    payload = mh._read_json(MARKET_INTELLIGENCE_STATE_PATH, {})
    return payload if isinstance(payload, dict) else {}


def _load_windows() -> dict[str, Any]:
    payload = mh._read_json(MARKET_INTELLIGENCE_WINDOWS_PATH, {"items": {}})
    if isinstance(payload, dict) and isinstance(payload.get("items"), dict):
        return payload
    state = _load_state()
    if isinstance(state.get("items"), dict):
        # One-time migration from the oversized state file created by the first patch.
        windows = {
            "cache_type": "market_intelligence_windows",
            "schema_version": 2,
            "updated_at": state.get("updated_at") or _utc_now_iso(),
            "source_state_migrated_at": _utc_now_iso(),
            "source_days": state.get("source_days"),
            "window_names": state.get("window_names", list(WINDOWS.keys())),
            "item_count": state.get("item_count") or len(state.get("items", {})),
            "items": state.get("items", {}),
        }
        _save_windows(windows)
        compact_state = {k: v for k, v in state.items() if k != "items"}
        compact_state.update({
            "items_removed_from_state": True,
            "windows_path": MARKET_INTELLIGENCE_WINDOWS_PATH,
            "compacted_at": _utc_now_iso(),
        })
        _save_state(compact_state)
        return windows
    return {"items": {}}


def _save_state(payload: dict[str, Any]) -> None:
    mh._ensure_dirs()
    compact = {k: v for k, v in payload.items() if k != "items"}
    mh._write_json(MARKET_INTELLIGENCE_STATE_PATH, compact)


def _save_windows(payload: dict[str, Any]) -> None:
    mh._ensure_dirs()
    mh._write_json(MARKET_INTELLIGENCE_WINDOWS_PATH, payload)


def build_derived_market_cache(market_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    mh._ensure_dirs()
    cache = market_cache if isinstance(market_cache, dict) else mh.load_market_cache()
    state = _load_state()
    windows_payload = _load_windows()
    state_items = windows_payload.get("items", {}) if isinstance(windows_payload.get("items"), dict) else {}
    source_items = cache.get("items", []) if isinstance(cache, dict) else []
    items = [row for raw in source_items if (row := _row_from_market_item(raw, state_items)) is not None]
    payload = {
        "cache_type": "derived_market_cache",
        "schema_version": 2,
        "updated_at": _utc_now_iso(),
        "snapshot_bucket": cache.get("snapshot_bucket") if isinstance(cache, dict) else None,
        "source_cache_updated_at": cache.get("updated_at") if isinstance(cache, dict) else None,
        "intelligence_state_updated_at": state.get("updated_at"),
        "intelligence_windows_updated_at": windows_payload.get("updated_at"),
        "intelligence_state_mode": state.get("mode", "collector_overlay"),
        "item_count": len(items),
        "items": sorted(items, key=lambda row: row.get("name", "").lower()),
    }
    mh._write_json(DERIVED_MARKET_CACHE_PATH, payload)
    return payload


def load_derived_market_cache() -> dict[str, Any]:
    payload = mh._read_json(DERIVED_MARKET_CACHE_PATH, {"items": [], "item_count": 0})
    return payload if isinstance(payload, dict) else {"items": [], "item_count": 0}


def _candidate_from_derived(row: dict[str, Any]) -> dict[str, Any] | None:
    buy_price = _to_int(row.get("buy"))
    sell_price = _to_int(row.get("sell"))
    profit_after_tax = _to_int(row.get("profit_after_tax"))
    roi_pct_after_tax = _to_float(row.get("roi_pct_after_tax"))
    volume_1h = _to_int(row.get("volume_1h"))
    if buy_price <= 0 or sell_price <= 0 or profit_after_tax <= 0 or roi_pct_after_tax < 0.10 or volume_1h < 100:
        return None
    buy_limit = _to_int(row.get("buy_limit"))
    liquidity_qty = max(1, int(volume_1h * 0.05))
    max_qty = min([cap for cap in (buy_limit, liquidity_qty) if cap > 0], default=liquidity_qty)
    if max_qty <= 0:
        return None
    risk = row.get("risk_evidence") if isinstance(row.get("risk_evidence"), dict) else {}
    stability_factor = max(0.05, min(1.0, _to_float(row.get("stability_score")) / 100.0))
    spike_penalty = 0.55 if risk.get("too_spiky") else 1.0
    persistence = max(0.05, min(1.0, _to_float(risk.get("spread_persistence_10d"), 0.5)))
    expected_time = 1.0 if row.get("fill_speed_band") == "fast" else 3.0 if row.get("fill_speed_band") == "medium" else 8.0 if row.get("fill_speed_band") == "slow" else 24.0
    market_proxy_fill_probability = round(max(0.05, min(0.98, persistence * stability_factor * spike_penalty)), 6)
    expected_profit = profit_after_tax * max_qty
    profit_per_hour = round(expected_profit / expected_time, 6) if expected_time > 0 else 0.0
    score = round(profit_per_hour * market_proxy_fill_probability * stability_factor, 6)
    item_id = _to_int(row.get("item_id") or row.get("id"))
    item_name = str(row.get("item_name") or row.get("name") or f"Item {item_id}")
    return {
        "plan_type": "flip",
        "item_id": item_id,
        "id": item_id,
        "item_name": item_name,
        "name": item_name,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "suggested_quantity": max_qty,
        "max_quantity": max_qty,
        "expected_profit": expected_profit,
        "profit_after_tax_per_item": profit_after_tax,
        "roi_pct_after_tax": roi_pct_after_tax,
        "expected_time_to_liquidity_hours": expected_time,
        "profit_per_hour": profit_per_hour,
        "market_proxy_fill_probability": market_proxy_fill_probability,
        "fill_model_source": "public_market_proxy",
        "empirical_fill_rate": None,
        "telemetry_sample_count": 0,
        "stability_factor": round(stability_factor, 6),
        "confidence_band": row.get("confidence_band"),
        "fill_speed_band": row.get("fill_speed_band"),
        "max_deployable_gp": _to_int(row.get("max_deployable_gp")),
        "risk_evidence": risk,
        "window_summary": {
            name: {
                "roi_avg": metric.get("roi_avg"),
                "roi_stddev": metric.get("roi_stddev"),
                "spread_positive_ratio": metric.get("spread_positive_ratio"),
                "spike_count": metric.get("spike_count"),
                "sample_count": metric.get("sample_count"),
                "quality": metric.get("quality"),
            }
            for name, metric in (row.get("windows") or {}).items()
            if isinstance(metric, dict)
        },
        "short_reason": "stable spread with usable liquidity" if not risk.get("too_spiky") else "profitable but spike risk is elevated",
        "score": score,
        "scoring_metadata": {
            "source": "recommendation_candidate_cache_v2",
            "volume_1h": volume_1h,
            "snapshot_volume_5m": _to_int(row.get("snapshot_volume_5m")),
            "stability_score": _to_float(row.get("stability_score")),
            "spread_gp": _to_int(row.get("spread_gp")),
            "spread_pct": _to_float(row.get("spread_pct")),
            "freshness_ts": row.get("freshness_ts"),
        },
    }


def build_recommendation_candidate_cache(derived_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    mh._ensure_dirs()
    source = derived_cache if isinstance(derived_cache, dict) else load_derived_market_cache()
    if not source.get("items"):
        source = build_derived_market_cache()
    items = [candidate for row in source.get("items", []) if (candidate := _candidate_from_derived(row)) is not None]
    items.sort(key=lambda row: _to_float(row.get("score")), reverse=True)
    payload = {
        "cache_type": "recommendation_candidate_cache",
        "schema_version": 2,
        "updated_at": _utc_now_iso(),
        "snapshot_bucket": source.get("snapshot_bucket"),
        "source_cache_updated_at": source.get("updated_at"),
        "candidate_count": len(items),
        "items": items,
        "candidates": items,
    }
    mh._write_json(RECOMMENDATION_CANDIDATE_CACHE_PATH, payload)
    return payload


def load_recommendation_candidate_cache() -> dict[str, Any]:
    payload = mh._read_json(RECOMMENDATION_CANDIDATE_CACHE_PATH, {"items": [], "candidate_count": 0})
    return payload if isinstance(payload, dict) else {"items": [], "candidate_count": 0}


def build_market_intelligence_caches(market_cache: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    derived = build_derived_market_cache(market_cache)
    candidates = build_recommendation_candidate_cache(derived)
    return derived, candidates


def _history_samples_for_windows(
    *,
    market_cache: dict[str, Any],
    window_names: Iterable[str],
    days: int,
) -> tuple[dict[int, dict[str, list[dict[str, Any]]]], int, int]:
    cache_items = market_cache.get("items", []) if isinstance(market_cache, dict) else []
    wanted_ids = {_to_int(item.get("id") or item.get("item_id")) for item in cache_items}
    wanted_ids = {item_id for item_id in wanted_ids if item_id > 0}
    now = _parse_ts(market_cache.get("snapshot_bucket") if isinstance(market_cache, dict) else None) or datetime.now(UTC)
    selected = {name: WINDOWS[name] for name in window_names if name in WINDOWS}
    window_cutoffs = {name: now - delta for name, delta in selected.items() if delta <= timedelta(days=days)}
    raw: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    scanned = 0
    used = 0
    for _source, record in mh._iter_history_records_with_source(days=days):
        scanned += 1
        item_id = _to_int(record.get("id"))
        if item_id not in wanted_ids:
            continue
        ts = _parse_ts(record.get("snapshot_ts"))
        if ts is None:
            continue
        low = _to_int(record.get("low"))
        high = _to_int(record.get("high"))
        if low <= 0 or high <= 0:
            continue
        if high < low:
            high, low = low, high
        sample = {
            "ts": ts.isoformat(),
            "low": low,
            "high": high,
            "spread": max(high - low, 0),
            "roi": _roi_pct(low, high),
            "volume": _to_int(record.get("recent_volume") or record.get("trade_volume") or record.get("volume")),
        }
        for name, cutoff in window_cutoffs.items():
            if ts >= cutoff:
                raw[item_id][name].append(sample)
                used += 1
    return raw, scanned, used


def rebuild_long_market_intelligence_windows(
    window_names: Iterable[str] = LONG_WINDOW_NAMES,
    days: int = DEFAULT_BACKFILL_DAYS,
    market_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild selected long-window analytics from raw history once.

    This repairs disposable derived intelligence only. It does not modify raw
    market history. Short windows are preserved; selected windows are replaced
    with freshly calculated, timestamp-validated metrics.
    """
    selected_names = [name for name in window_names if name in WINDOWS]
    if not selected_names:
        selected_names = list(LONG_WINDOW_NAMES)
    max_days = max(WINDOWS[name] for name in selected_names).days or days
    days = max(days, max_days)
    days = max(1, min(MAX_BACKFILL_DAYS, int(days or DEFAULT_BACKFILL_DAYS)))

    cache = market_cache if isinstance(market_cache, dict) else mh.load_market_cache()
    windows_payload = _load_windows()
    items = windows_payload.get("items", {}) if isinstance(windows_payload.get("items"), dict) else {}
    invalid_before = _count_invalid_windows(items, selected_names)

    raw, scanned, used = _history_samples_for_windows(
        market_cache=cache,
        window_names=selected_names,
        days=days,
    )

    rebuilt_item_count = 0
    for item_id, windows in raw.items():
        key = str(item_id)
        entry = items.get(key) if isinstance(items.get(key), dict) else {"item_id": item_id, "windows": {}}
        entry_windows = entry.get("windows", {}) if isinstance(entry.get("windows"), dict) else {}
        for name in selected_names:
            samples = windows.get(name, [])
            metric = _window_metrics(samples, None)
            if not _window_metric_is_valid(metric):
                metric = {"sample_count": 0, "quality": "invalid_rebuilt_window"}
            entry_windows[name] = metric
        entry["item_id"] = item_id
        entry["windows"] = _sanitize_windows(entry_windows)
        items[key] = entry
        rebuilt_item_count += 1

    # Remove any still-invalid selected windows from items that had no fresh raw samples.
    for entry in items.values():
        if not isinstance(entry, dict):
            continue
        entry_windows = entry.get("windows", {}) if isinstance(entry.get("windows"), dict) else {}
        for name in selected_names:
            metric = entry_windows.get(name)
            if metric is not None and not _window_metric_is_valid(metric):
                entry_windows.pop(name, None)
        entry["windows"] = _sanitize_windows(entry_windows)

    invalid_after = _count_invalid_windows(items, selected_names)
    now_iso = _utc_now_iso()
    windows_payload.update({
        "cache_type": "market_intelligence_windows",
        "schema_version": 2,
        "mode": "long_window_repair",
        "updated_at": now_iso,
        "long_windows_rebuilt_at": now_iso,
        "long_windows_rebuilt": selected_names,
        "long_window_repair_scanned_history_rows": scanned,
        "long_window_repair_used_window_samples": used,
        "item_count": len(items),
        "items": items,
    })
    _save_windows(windows_payload)

    state = _load_state()
    compact_state = {k: v for k, v in state.items() if k != "items"}
    compact_state.update({
        "cache_type": "market_intelligence_state",
        "schema_version": 2,
        "mode": "long_window_repair",
        "updated_at": now_iso,
        "windows_path": MARKET_INTELLIGENCE_WINDOWS_PATH,
        "long_windows_rebuilt_at": now_iso,
        "long_windows_rebuilt": selected_names,
        "long_window_invalid_before": invalid_before,
        "long_window_invalid_after": invalid_after,
        "long_window_repair_scanned_history_rows": scanned,
        "long_window_repair_used_window_samples": used,
    })
    _save_state(compact_state)

    derived, candidates = build_market_intelligence_caches(cache)
    return {
        "status": "ok" if invalid_after == 0 else "invalid_windows_remaining",
        "raw_history_modified": False,
        "rebuilt_windows": selected_names,
        "rebuilt_item_count": rebuilt_item_count,
        "invalid_before": invalid_before,
        "invalid_after": invalid_after,
        "scanned_history_rows": scanned,
        "used_window_samples": used,
        "windows_path": MARKET_INTELLIGENCE_WINDOWS_PATH,
        "state_path": MARKET_INTELLIGENCE_STATE_PATH,
        "derived_item_count": derived.get("item_count", 0),
        "candidate_count": candidates.get("candidate_count", 0),
    }


def backfill_market_intelligence(days: int = DEFAULT_BACKFILL_DAYS, market_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    days = max(1, min(MAX_BACKFILL_DAYS, int(days or DEFAULT_BACKFILL_DAYS)))
    cache = market_cache if isinstance(market_cache, dict) else mh.load_market_cache()
    cache_items = cache.get("items", []) if isinstance(cache, dict) else []
    wanted_ids = {_to_int(item.get("id") or item.get("item_id")) for item in cache_items}
    wanted_ids = {item_id for item_id in wanted_ids if item_id > 0}
    now = _parse_ts(cache.get("snapshot_bucket") if isinstance(cache, dict) else None) or datetime.now(UTC)
    window_cutoffs = {name: now - delta for name, delta in WINDOWS.items() if delta <= timedelta(days=days)}
    raw: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    scanned = 0
    used = 0
    for _source, record in mh._iter_history_records_with_source(days=days):
        scanned += 1
        item_id = _to_int(record.get("id"))
        if item_id not in wanted_ids:
            continue
        ts = _parse_ts(record.get("snapshot_ts"))
        if ts is None:
            continue
        low = _to_int(record.get("low"))
        high = _to_int(record.get("high"))
        if low <= 0 or high <= 0:
            continue
        if high < low:
            high, low = low, high
        sample = {
            "ts": ts.isoformat(),
            "low": low,
            "high": high,
            "spread": max(high - low, 0),
            "roi": _roi_pct(low, high),
            "volume": _to_int(record.get("recent_volume") or record.get("trade_volume") or record.get("volume")),
        }
        for name, cutoff in window_cutoffs.items():
            if ts >= cutoff:
                raw[item_id][name].append(sample)
                used += 1
    state_items: dict[str, Any] = {}
    for item_id, windows in raw.items():
        state_items[str(item_id)] = {
            "item_id": item_id,
            "windows": {name: _window_metrics(samples, now) for name, samples in windows.items()},
        }
    state = {
        "cache_type": "market_intelligence_state",
        "schema_version": 2,
        "mode": "full_backfill",
        "updated_at": _utc_now_iso(),
        "source_days": days,
        "window_names": list(window_cutoffs.keys()),
        "scanned_history_rows": scanned,
        "used_window_samples": used,
        "item_count": len(state_items),
        "items": state_items,
    }
    windows_payload = {
        "cache_type": "market_intelligence_windows",
        "schema_version": 2,
        "mode": "full_backfill",
        "updated_at": state["updated_at"],
        "source_days": days,
        "window_names": list(window_cutoffs.keys()),
        "scanned_history_rows": scanned,
        "used_window_samples": used,
        "item_count": len(state_items),
        "items": state_items,
    }
    _save_windows(windows_payload)
    state = {k: v for k, v in state.items() if k != "items"}
    state["windows_path"] = MARKET_INTELLIGENCE_WINDOWS_PATH
    _save_state(state)
    derived, candidates = build_market_intelligence_caches(cache)
    return {
        "state": {k: v for k, v in state.items() if k != "items"},
        "derived_item_count": derived.get("item_count", 0),
        "candidate_count": candidates.get("candidate_count", 0),
        "derived_path": DERIVED_MARKET_CACHE_PATH,
        "candidate_path": RECOMMENDATION_CANDIDATE_CACHE_PATH,
        "state_path": MARKET_INTELLIGENCE_STATE_PATH,
    }
