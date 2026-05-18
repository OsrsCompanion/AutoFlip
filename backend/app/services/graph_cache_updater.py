from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

WINDOWS: tuple[str, ...] = ("day", "week", "month", "3month", "year")
THROTTLE_MODE = "day_5m_week_15m_month_60m_3month_daily_year_weekly"
WINDOW_DAYS: dict[str, int] = {"day": 1, "week": 7, "month": 31, "3month": 360, "year": 365}
TARGET_POINTS: dict[str, int] = {"day": 288, "week": 336, "month": 360, "3month": 360, "year": 365}
GRAPH_WRITE_BUDGET = int(os.getenv("OSRS_GRAPH_WRITE_BUDGET", "1000"))
HOT_ITEMS_LOG_PATH = (
    Path(os.getenv("OSRS_FLIP_DATA_ROOT", "/mnt/nvme/autoflip-data"))
    / "cache"
    / "hot_items.jsonl"
)
HOT_ITEM_PRIORITY_LIMIT = int(os.getenv("OSRS_HOT_ITEM_PRIORITY_LIMIT", "500"))
HOT_ITEM_MAX_AGE_SECONDS = int(os.getenv("OSRS_HOT_ITEM_MAX_AGE_SECONDS", "21600"))
STALE_GRAPH_PRIORITY_SECONDS = int(os.getenv("OSRS_STALE_GRAPH_PRIORITY_SECONDS", "3600"))
STARVED_GRAPH_PRIORITY_SECONDS = int(os.getenv("OSRS_STARVED_GRAPH_PRIORITY_SECONDS", "7200"))
HARD_MAX_STALE_GRAPH_SECONDS = int(os.getenv("OSRS_HARD_MAX_STALE_GRAPH_SECONDS", "21600"))
SELF_HEAL_DAY_MIN_POINTS = int(os.getenv("OSRS_SELF_HEAL_DAY_MIN_POINTS", "12"))
SELF_HEAL_BYPASS_BUDGET = int(os.getenv("OSRS_SELF_HEAL_BYPASS_BUDGET", "0"))
SELF_HEAL_BYPASS_INTERVAL_SECONDS = int(os.getenv("OSRS_SELF_HEAL_BYPASS_INTERVAL_SECONDS", "1800"))



def _data_root() -> Path:
    env_root = os.getenv("OSRS_FLIP_DATA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path("/mnt/nvme/autoflip-data").resolve()


def _cache_root() -> Path:
    return (_data_root() / "cache" / "history_graphs").resolve()


def _marker_path() -> Path:
    return (_cache_root() / "incremental_latest.json").resolve()


def _logs_root() -> Path:
    return (_data_root() / "logs").resolve()


def _load_hot_item_ids(limit: int = HOT_ITEM_PRIORITY_LIMIT) -> set[int]:
    """Load recently viewed graph item IDs for collector-side prioritization."""
    if limit <= 0:
        return set()

    path = HOT_ITEMS_LOG_PATH
    if not path.exists():
        return set()

    cutoff = _utc_now().timestamp() - HOT_ITEM_MAX_AGE_SECONDS
    hot_ids: list[int] = []

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-5000:]
    except Exception:
        return set()

    for line in reversed(lines):
        if len(hot_ids) >= limit:
            break
        try:
            payload = json.loads(line)
            item_id = _to_int(payload.get("item_id"))
            ts_text = payload.get("ts")
            ts = _parse_ts(ts_text)
            if item_id and ts and ts.timestamp() >= cutoff:
                hot_ids.append(item_id)
        except Exception:
            continue

    return set(hot_ids)


def _graph_cache_window_age_seconds(item_id: int, window: str, now_ts: float) -> float:
    """Return selected window graph cache age, treating missing/corrupt metadata as very old."""
    if window not in WINDOWS:
        return 0.0

    path = _cache_root() / window / f"{item_id}.json"
    payload = _read_payload(path)
    updated_at = _parse_ts(payload.get("graph_cache_updated_at"))

    if updated_at is None:
        return 999999999.0

    return max(0.0, now_ts - updated_at.timestamp())


def _graph_cache_age_seconds(item_id: int, windows: tuple[str, ...], now_ts: float) -> float:
    """Return oldest selected graph cache age for stale-priority sorting."""
    oldest_age = 0.0

    for window in windows:
        age = _graph_cache_window_age_seconds(item_id, window, now_ts)
        if age >= 999999999.0:
            return age
        if age > oldest_age:
            oldest_age = age

    return oldest_age


def _graph_cache_needs_self_healing(item_id: int, windows: tuple[str, ...]) -> bool:
    """Return True for sparse/corrupt day caches that should be rebuilt from bounded history.

    Incremental updates are O(new snapshot rows) and should remain the normal path.
    This helper only flags day cache files that are too sparse or structurally
    weak, so the collector can rebuild that one item from the bounded 24h
    history loader instead of preserving a bad one-point cache forever.
    """
    if "day" not in windows or item_id <= 0:
        return False

    path = _cache_root() / "day" / f"{item_id}.json"
    payload = _read_payload(path)
    points = payload.get("points")

    if not payload or not isinstance(points, list):
        return True

    point_count = _to_int(payload.get("point_count") or len(points))
    raw_point_count = _to_int(payload.get("raw_point_count"))

    if payload.get("corrupt_graph_cache_repaired"):
        return True

    if payload.get("window_strategy") == "day_repaired_from_raw_snapshot_5m" and point_count <= SELF_HEAL_DAY_MIN_POINTS:
        return True

    # One/few-point day files can be produced by incremental append against an
    # already-bad cache. Rebuild them from the bounded day-history loader.
    if point_count <= SELF_HEAL_DAY_MIN_POINTS:
        return True

    # Missing timing metadata makes freshness and fairness unreliable.
    if not payload.get("graph_cache_updated_at") and not payload.get("graph_cache_built_at"):
        return True

    # Payload claims raw history but does not retain enough plotted points.
    if raw_point_count > SELF_HEAL_DAY_MIN_POINTS and point_count <= SELF_HEAL_DAY_MIN_POINTS:
        return True

    return False


def _item_priority_score(item: dict[str, Any]) -> float:
    """Score item importance using existing cache fields only.

    Higher score means the graph should be refreshed earlier within the fixed
    write budget. This keeps staples like runes ahead of low-volume junk while
    still giving high-value rare items meaningful priority.
    """
    snapshot_volume = _to_float(item.get("snapshot_volume_5m"))
    recent_volume = _to_float(item.get("recent_volume"))
    day_volume = _to_float(item.get("day_volume"))
    week_volume = _to_float(item.get("week_volume"))
    month_volume = _to_float(item.get("month_volume"))
    avg_daily_volume = _to_float(item.get("avg_daily_volume"))

    buy_price = _to_float(item.get("buy_price") or item.get("buy") or item.get("low"))
    sell_price = _to_float(item.get("sell_price") or item.get("sell") or item.get("high"))
    value = max(buy_price, sell_price)

    # Liquidity dominates: nature/death/blood/fire runes, Zulrah scales, etc.
    liquidity_score = (
        snapshot_volume * 25.0
        + recent_volume * 5.0
        + day_volume * 1.0
        + week_volume * 0.15
        + month_volume * 0.03
        + avg_daily_volume * 1.5
    )

    # Value matters, but should not let dead expensive items beat active staples.
    if value >= 100_000_000:
        value_score = 15_000_000.0
    elif value >= 10_000_000:
        value_score = 5_000_000.0
    elif value >= 1_000_000:
        value_score = 1_000_000.0
    else:
        value_score = value * 0.05

    low_volume_penalty = 0.0
    if day_volume < 1_000 and week_volume < 10_000 and value < 1_000_000:
        low_volume_penalty = 10_000_000.0

    return liquidity_score + value_score - low_volume_penalty


def _prioritize_graph_items(
    items: list[dict[str, Any]],
    hot_item_ids: set[int],
    selected_windows: tuple[str, ...],
) -> tuple[list[dict[str, Any]], int, int, int, int]:
    """Prioritize graph items while preventing low-score item starvation.

    The write budget is smaller than the item universe, so a pure
    liquidity/value sort can repeatedly refresh the same strong items while
    thin items never receive a cache file or freshness marker.  Keep hot items
    first, then reserve a deterministic rotating lane for starved items.
    """
    now_ts = _utc_now().timestamp()
    total_items = max(1, len(items))
    rotation_offset = int(now_ts // 300) % total_items
    stale_count = 0
    starved_count = 0
    hard_max_stale_count = 0
    self_heal_count = 0
    scored: list[tuple[int, float, float, float, int, dict[str, Any]]] = []

    for index, item in enumerate(items):
        item_id = _to_int(item.get("id"))
        is_hot = item_id in hot_item_ids
        age_seconds = _graph_cache_age_seconds(item_id, selected_windows, now_ts)
        is_stale = age_seconds >= STALE_GRAPH_PRIORITY_SECONDS
        is_starved = age_seconds >= STARVED_GRAPH_PRIORITY_SECONDS
        is_hard_max_stale = age_seconds >= HARD_MAX_STALE_GRAPH_SECONDS
        needs_self_healing = _graph_cache_needs_self_healing(item_id, selected_windows)
        item_score = _item_priority_score(item)
        rotation_rank = float((index - rotation_offset) % total_items)

        if is_stale:
            stale_count += 1
        if is_starved:
            starved_count += 1
        if is_hard_max_stale:
            hard_max_stale_count += 1
        if needs_self_healing:
            self_heal_count += 1

        if is_hot and is_stale:
            # Recently viewed stale graphs still win.
            scored.append((0, -age_seconds, -item_score, rotation_rank, index, item))
        elif is_hot:
            scored.append((1, -item_score, -age_seconds, rotation_rank, index, item))
        elif is_hard_max_stale:
            # Hard stale override: items past the maximum allowed age must beat
            # normal liquidity/value scoring, even with zero volume. Age wins
            # here so extremely stale files recover before newer starved files.
            scored.append((2, -age_seconds, rotation_rank, -item_score, index, item))
        elif is_starved:
            # Fairness lane: rotate through neglected/missing graph files so
            # low-value items cannot be permanently excluded by score.
            scored.append((3, rotation_rank, -age_seconds, -item_score, index, item))
        elif is_stale:
            # Stale-but-not-starved items prefer age before score.
            scored.append((4, -age_seconds, rotation_rank, -item_score, index, item))
        else:
            scored.append((5, -item_score, rotation_rank, -age_seconds, index, item))

    scored.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    return [row[5] for row in scored], stale_count, starved_count, hard_max_stale_count, self_heal_count


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp_path, path)


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _point_from_item(item: dict[str, Any], snapshot_bucket: str) -> dict[str, Any] | None:
    item_id = _to_int(item.get("id"))
    if item_id <= 0:
        return None

    low = _to_float(
        item.get("low")
        or item.get("buy_price")
    )
    high = _to_float(
        item.get("high")
        or item.get("sell_price")
    )
    if high <= 0 and low <= 0:
        return None
    if high <= 0 < low:
        high = low
    elif low <= 0 < high:
        low = high
    if high < low:
        high, low = low, high

    recent_volume = _to_int(item.get("recent_volume") or item.get("volume"))
    trade_volume = _to_int(item.get("trade_volume"))
    buy_limit = _to_int(item.get("buy_limit") or item.get("limit"))

    return {
        "snapshot_ts": snapshot_bucket,
        "low": round(low, 3),
        "high": round(high, 3),
        "recent_volume": recent_volume,
        "volume": trade_volume or recent_volume,
        "trade_volume": trade_volume,
        "buy_limit": buy_limit,
        "sample_count": 1,
        "min_low": int(round(low)),
        "max_high": int(round(high)),
        "is_compacted": False,
        "point_mode": "snapshot",
        "source": "snapshot_5m",
    }


def _thin_points_evenly(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if max_points <= 0 or len(points) <= max_points:
        return points
    if max_points == 1:
        return [points[-1]]
    last_index = len(points) - 1
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i in range(max_points):
        idx = round(i * last_index / (max_points - 1))
        if idx in seen:
            continue
        seen.add(idx)
        selected.append(points[idx])
    return selected


def _long_bucket_key(ts: datetime, window: str) -> str:
    if window == "year":
        # Weekly buckets keep year graphs bounded and deterministic.
        start = (ts - timedelta(days=ts.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat()
    if window == "3month":
        # Daily buckets keep 3M graphs bounded and deterministic.
        return ts.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return str(ts.isoformat())


def _aggregate_long_window_points(points: list[dict[str, Any]], window: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        ts = _parse_ts(point.get("snapshot_ts"))
        if not ts:
            continue
        grouped.setdefault(_long_bucket_key(ts, window), []).append(point)

    out: list[dict[str, Any]] = []
    for bucket_ts in sorted(grouped.keys()):
        group = grouped[bucket_ts]
        lows = [_to_float(p.get("low")) for p in group if _to_float(p.get("low")) > 0]
        highs = [_to_float(p.get("high")) for p in group if _to_float(p.get("high")) > 0]
        if not lows and not highs:
            continue
        if not lows:
            lows = list(highs)
        if not highs:
            highs = list(lows)
        recent_volume = sum(_to_int(p.get("recent_volume") or p.get("volume")) for p in group)
        trade_volume = sum(_to_int(p.get("trade_volume")) for p in group)
        out.append({
            **group[-1],
            "snapshot_ts": bucket_ts,
            "low": round(sum(lows) / len(lows), 3),
            "high": round(sum(highs) / len(highs), 3),
            "min_low": int(round(min(lows))),
            "max_high": int(round(max(highs))),
            "recent_volume": recent_volume,
            "volume": trade_volume or recent_volume,
            "trade_volume": trade_volume,
            "sample_count": max(sum(_to_int(p.get("sample_count")) or 1 for p in group), 1),
            "is_compacted": True,
            "point_mode": f"{window}_compressed",
            "source": f"{window}_incremental_compressed",
        })
    return out


def _dedupe_sort_trim_points(
    points: list[dict[str, Any]],
    *,
    newest_ts: datetime,
    window: str,
) -> list[dict[str, Any]]:
    cutoff = newest_ts - timedelta(days=WINDOW_DAYS.get(window, 31))
    by_ts: dict[str, dict[str, Any]] = {}

    for point in points:
        if not isinstance(point, dict):
            continue
        ts_text = point.get("snapshot_ts")
        ts = _parse_ts(ts_text)
        if not ts or ts < cutoff:
            continue
        by_ts[str(ts_text)] = point

    sorted_points = [by_ts[key] for key in sorted(by_ts.keys())]

    if window in {"3month", "year"}:
        # Preserve existing compacted buckets and only aggregate new raw points.
        # Re-aggregating already-compacted buckets collapses long windows down to
        # one bucket and destroys historical dots.
        compacted_points = [
            point for point in sorted_points
            if isinstance(point, dict) and bool(point.get("is_compacted"))
        ]
        raw_points = [
            point for point in sorted_points
            if isinstance(point, dict) and not bool(point.get("is_compacted"))
        ]
        newly_compacted = _aggregate_long_window_points(raw_points, window)

        merged: dict[str, dict[str, Any]] = {}
        for point in [*compacted_points, *newly_compacted]:
            ts_text = str(point.get("snapshot_ts") or "")
            if ts_text:
                merged[ts_text] = point
        sorted_points = [merged[key] for key in sorted(merged.keys())]

    if window == "day":
        return sorted_points
    return _thin_points_evenly(sorted_points, TARGET_POINTS.get(window, 288))


def _self_heal_bypass_marker_path() -> Path:
    return (_cache_root() / "self_heal_bypass_latest.json").resolve()


def _self_heal_bypass_due(snapshot_bucket: str) -> bool:
    """Throttle expensive repair scans so the collector stays thermally safe."""
    if SELF_HEAL_BYPASS_INTERVAL_SECONDS <= 0:
        return True

    payload = _read_payload(_self_heal_bypass_marker_path())
    updated_at = _parse_ts(payload.get("updated_at"))
    if updated_at is None:
        return True

    return (_utc_now() - updated_at).total_seconds() >= SELF_HEAL_BYPASS_INTERVAL_SECONDS


def _write_self_heal_bypass_marker(payload: dict[str, Any]) -> None:
    marker = dict(payload)
    marker["updated_at"] = _utc_now().isoformat()
    _write_json_atomic(_self_heal_bypass_marker_path(), marker)


def _line_may_contain_item_id(line: str, item_ids: set[int]) -> bool:
    """Cheap prefilter before json.loads for shared JSONL history files."""
    for item_id in item_ids:
        text = str(item_id)
        if f'"id": {text}' in line or f'"id":{text}' in line or f'"item_id": {text}' in line or f'"item_id":{text}' in line:
            return True
    return False


def _batch_load_day_rows_for_items(item_ids: set[int]) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    """Load day rows for several item IDs with one shared pass over recent files.

    This replaces the unsafe N-items × N-files rebuild pattern. The scan still
    walks the recent day JSONL files, but it does so once for the whole bypass
    batch and groups matching rows by item ID.
    """
    started = time.perf_counter()
    rows_by_item: dict[int, list[dict[str, Any]]] = {item_id: [] for item_id in item_ids}
    meta = {
        "files_scanned": 0,
        "rows_scanned": 0,
        "rows_matched": 0,
        "sources": {},
    }

    if not item_ids:
        meta["elapsed_ms"] = 0
        return rows_by_item, meta

    try:
        from app.services import market_history as mh
    except Exception as exc:
        meta["error"] = f"market_history_import_failed: {exc}"
        meta["elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
        return rows_by_item, meta

    reference_now = _utc_now()
    cutoff = reference_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

    source_dirs = [
        ("snapshot_5m", getattr(mh, "SNAPSHOT_DIR", "")),
        ("trade_event", getattr(mh, "EVENT_DIR", "")),
    ]

    for source_name, base_dir in source_dirs:
        if not base_dir:
            continue
        try:
            files = mh._iter_history_files(base_dir, cutoff=cutoff)  # type: ignore[attr-defined]
        except Exception:
            files = []
        source_meta = {"files": len(files), "rows_scanned": 0, "rows_matched": 0}
        for path in files:
            meta["files_scanned"] += 1
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        meta["rows_scanned"] += 1
                        source_meta["rows_scanned"] += 1
                        if not _line_may_contain_item_id(line, item_ids):
                            continue
                        try:
                            raw = json.loads(line)
                        except Exception:
                            continue
                        try:
                            normalized = mh._normalize_history_row(raw)  # type: ignore[attr-defined]
                        except Exception:
                            normalized = None
                        if not normalized:
                            continue
                        item_id = _to_int(normalized.get("id") or normalized.get("item_id"))
                        if item_id not in item_ids:
                            continue
                        if source_name == "trade_event":
                            normalized["source"] = "trade_event"
                            normalized["is_compacted"] = False
                        else:
                            normalized["source"] = "snapshot_5m"
                            normalized["is_compacted"] = bool(normalized.get("is_compacted", False))
                        rows_by_item.setdefault(item_id, []).append(normalized)
                        meta["rows_matched"] += 1
                        source_meta["rows_matched"] += 1
            except FileNotFoundError:
                continue
            except Exception:
                continue
        meta["sources"][source_name] = source_meta

    for item_id, rows in rows_by_item.items():
        try:
            rows_by_item[item_id] = mh._dedupe_history_rows(rows)  # type: ignore[attr-defined]
        except Exception:
            pass
        rows_by_item[item_id].sort(key=lambda row: _parse_ts(row.get("snapshot_ts")) or datetime.min.replace(tzinfo=UTC))

    meta["elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
    return rows_by_item, meta


def _build_day_payload_from_rows(item_id: int, rows: list[dict[str, Any]], path: Path, batch_meta: dict[str, Any]) -> dict[str, Any] | None:
    if not rows:
        return None

    try:
        from app.services import market_history as mh
        snapshot_rows = [row for row in rows if not row.get("side") and row.get("source") != "trade_event"]
        event_rows = [row for row in rows if row.get("side") or row.get("source") == "trade_event"]
        points = mh._aggregate_history_buckets(  # type: ignore[attr-defined]
            [*snapshot_rows, *event_rows],
            bucket_minutes=5,
            point_mode="intraday_hybrid",
            source="intraday_5m",
        )
    except Exception:
        points = []

    if not points:
        return None

    timestamps = [str(p.get("snapshot_ts")) for p in points if isinstance(p, dict) and p.get("snapshot_ts")]
    return {
        "points": points,
        "raw_point_count": len(rows),
        "point_count": len(points),
        "target_points": TARGET_POINTS.get("day", 288),
        "point_source": "intraday_5m",
        "window_strategy": "snapshot_5m_plus_trade_event_extremes",
        "storage_mode": "snapshot+events",
        "contains_trade_events": any(row.get("side") or row.get("source") == "trade_event" for row in rows),
        "graph_source_counts": {
            "snapshot_rows": len([row for row in rows if not row.get("side") and row.get("source") != "trade_event"]),
            "event_rows": len([row for row in rows if row.get("side") or row.get("source") == "trade_event"]),
        },
        "first_point_ts": timestamps[0] if timestamps else None,
        "last_point_ts": timestamps[-1] if timestamps else None,
        "latest_point_ts": timestamps[-1] if timestamps else None,
        "served_from_graph_cache": False,
        "served_from_memory_cache": False,
        "graph_cache_path": str(path),
        "graph_cache_updated_at": _utc_now().isoformat(),
        "graph_cache_update_mode": "self_healing_batch_rebuild_from_single_shared_scan",
        "self_healed_graph_cache": True,
        "self_heal_min_points": SELF_HEAL_DAY_MIN_POINTS,
        "self_heal_batch_meta": batch_meta,
    }


def _run_self_heal_bypass_lane(
    items: list[dict[str, Any]],
    selected_windows: tuple[str, ...],
    snapshot_bucket: str,
) -> tuple[int, int, list[int], dict[str, Any]]:
    """Repair sparse day caches using one shared recent-history scan.

    Previous versions called the bounded history loader once per item. On the
    Pi that scanned ~5M rows per repaired item and caused a thermal rebuild
    storm. This lane picks a small batch, scans recent history once, groups rows
    by item ID, and writes only caches that improve.
    """
    if "day" not in selected_windows or SELF_HEAL_BYPASS_BUDGET <= 0:
        return 0, 0, [], {"skipped": "disabled_or_no_day_window"}

    if not _self_heal_bypass_due(snapshot_bucket):
        return 0, 0, [], {"skipped": "interval_throttle", "interval_seconds": SELF_HEAL_BYPASS_INTERVAL_SECONDS}

    now_ts = _utc_now().timestamp()
    candidates: list[tuple[float, int, int]] = []

    for index, item in enumerate(items):
        item_id = _to_int(item.get("id"))
        if item_id <= 0:
            continue
        if not _graph_cache_needs_self_healing(item_id, ("day",)):
            continue
        age_seconds = _graph_cache_window_age_seconds(item_id, "day", now_ts)
        candidates.append((-age_seconds, index, item_id))

    candidates.sort()
    selected_ids = [item_id for _neg_age, _index, item_id in candidates[:SELF_HEAL_BYPASS_BUDGET]]
    if not selected_ids:
        meta = {"candidate_count": 0}
        _write_self_heal_bypass_marker({"snapshot_bucket": snapshot_bucket, **meta})
        return 0, 0, [], meta

    rows_by_item, batch_meta = _batch_load_day_rows_for_items(set(selected_ids))
    attempted = len(selected_ids)
    repaired = 0
    repaired_ids: list[int] = []

    for item_id in selected_ids:
        path = _cache_root() / "day" / f"{item_id}.json"
        existing = _read_payload(path)
        existing_points = existing.get("points")
        existing_count = len(existing_points) if isinstance(existing_points, list) else 0
        payload = _build_day_payload_from_rows(item_id, rows_by_item.get(item_id, []), path, batch_meta)
        if not payload:
            continue
        if _to_int(payload.get("point_count")) <= existing_count and existing_count > 0:
            continue
        _write_json_atomic(path, payload)
        repaired += 1
        repaired_ids.append(item_id)

    meta = {
        "candidate_count": len(candidates),
        "selected_count": len(selected_ids),
        "batch_files_scanned": batch_meta.get("files_scanned", 0),
        "batch_rows_scanned": batch_meta.get("rows_scanned", 0),
        "batch_rows_matched": batch_meta.get("rows_matched", 0),
        "batch_elapsed_ms": batch_meta.get("elapsed_ms", 0),
        "interval_seconds": SELF_HEAL_BYPASS_INTERVAL_SECONDS,
    }
    _write_self_heal_bypass_marker({"snapshot_bucket": snapshot_bucket, **meta, "repaired": repaired, "repaired_ids": repaired_ids[:25]})
    return attempted, repaired, repaired_ids, meta


def _update_window_cache(item_id: int, point: dict[str, Any], snapshot_dt: datetime, window: str, *, force_append_unchanged: bool = False) -> bool:
    path = _cache_root() / window / f"{item_id}.json"
    payload = _read_payload(path)
    existing_points = payload.get("points")
    if not isinstance(existing_points, list):
        existing_points = []

    # Sparse/corrupt day caches are repaired by _run_self_heal_bypass_lane()
    # using one shared batch scan. Do NOT call the bounded history loader here;
    # doing that per item caused repeated multi-million-row scans and overheated
    # the Pi. The normal path may still append the current snapshot cheaply.

    # Delta-aware write suppression:
    # Skip rewriting graph cache files when the newest point has no meaningful
    # market change versus the existing last point.
    last_point = existing_points[-1] if existing_points else None

    if isinstance(last_point, dict):
        unchanged = (
            _to_int(last_point.get("price")) == _to_int(point.get("price"))
            and _to_int(last_point.get("high")) == _to_int(point.get("high"))
            and _to_int(last_point.get("low")) == _to_int(point.get("low"))
            and _to_int(last_point.get("volume")) == _to_int(point.get("volume"))
        )

        if unchanged and not force_append_unchanged:
            return False

    points = _dedupe_sort_trim_points([*existing_points, point], newest_ts=snapshot_dt, window=window)
    timestamps = [str(p.get("snapshot_ts")) for p in points if p.get("snapshot_ts")]

    if not payload:
        payload = {
            "target_points": TARGET_POINTS.get(window, 288),
            "point_source": "snapshot_5m",
            "window_strategy": "incremental_append_trim",
            "storage_mode": "snapshot_only",
            "contains_trade_events": False,
        }

    payload["points"] = points
    payload["raw_point_count"] = len(points)
    payload["point_count"] = len(points)
    payload["target_points"] = payload.get("target_points") or TARGET_POINTS.get(window, 288)
    payload["first_point_ts"] = timestamps[0] if timestamps else None
    payload["last_point_ts"] = timestamps[-1] if timestamps else None
    payload["latest_point_ts"] = timestamps[-1] if timestamps else None
    payload["served_from_graph_cache"] = False
    payload["served_from_memory_cache"] = False
    payload["graph_cache_path"] = str(path)
    payload["graph_cache_updated_at"] = _utc_now().isoformat()
    payload["graph_cache_update_mode"] = "incremental_append_trim"
    payload.setdefault("point_source", "snapshot_5m")
    payload.setdefault("window_strategy", "incremental_append_trim")
    payload.setdefault("storage_mode", "snapshot_only")
    payload.setdefault("contains_trade_events", False)

    _write_json_atomic(path, payload)
    return True


def refresh_single_item_graph_from_market_cache(item_id: int, window: str) -> dict[str, Any]:
    """Refresh exactly one item/window graph from current market cache.

    This is the only allowed web-triggered graph write path:
    - no live market fetch
    - no full cache rebuild
    - no raw history scan
    - one item
    - one window
    """
    if window not in WINDOWS:
        return {
            "urgent_single_item_refresh": False,
            "urgent_refresh_status": "invalid_window",
            "item_id": item_id,
            "window": window,
        }

    market_cache_path = _data_root() / "cache" / "market_cache.json"
    payload = _read_payload(market_cache_path)
    snapshot_bucket = str(payload.get("snapshot_bucket") or "")
    snapshot_dt = _parse_ts(snapshot_bucket)

    if snapshot_dt is None:
        return {
            "urgent_single_item_refresh": False,
            "urgent_refresh_status": "missing_snapshot_bucket",
            "item_id": item_id,
            "window": window,
        }

    items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []

    target_id = _to_int(item_id)
    target_item = None

    for item in items:
        if _to_int(item.get("id")) == target_id:
            target_item = item
            break

    if target_item is None:
        return {
            "urgent_single_item_refresh": False,
            "urgent_refresh_status": "item_not_found",
            "item_id": item_id,
            "window": window,
            "snapshot_bucket": snapshot_bucket,
        }

    point = _point_from_item(target_item, snapshot_bucket)
    if point is None:
        return {
            "urgent_single_item_refresh": False,
            "urgent_refresh_status": "point_unavailable",
            "item_id": item_id,
            "window": window,
            "snapshot_bucket": snapshot_bucket,
        }

    changed = _update_window_cache(target_id, point, snapshot_dt, window, force_append_unchanged=True)

    return {
        "urgent_single_item_refresh": True,
        "urgent_refresh_status": "updated" if changed else "unchanged",
        "item_id": target_id,
        "window": window,
        "snapshot_bucket": snapshot_bucket,
        "changed": changed,
    }


def graph_cache_incremental_current(snapshot_bucket: str) -> bool:
    """Return True when the incremental updater has already processed this bucket.

    This marker prevents the collector's same-bucket fast path from rewriting
    thousands of graph files every minute. It also lets a newly deployed updater
    catch up once if the market cache already has the current bucket.
    """
    payload = _read_payload(_marker_path())
    return str(payload.get("snapshot_bucket") or "") == str(snapshot_bucket)



def _windows_for_snapshot_bucket(snapshot_dt: datetime) -> tuple[str, ...]:
    """Select which graph windows should be rewritten for this snapshot bucket.

    Day remains fully current. Longer windows are throttled to reduce Pi write I/O:
    - day: every snapshot bucket
    - week: every 15 minutes
    - month: every hour
    - 3month: every 6 hours, folded into one daily bucket
    - year: daily, folded into one weekly bucket
    """
    selected = ["day"]
    if snapshot_dt.minute % 15 == 0:
        selected.append("week")
    if snapshot_dt.minute == 0:
        selected.append("month")
        if snapshot_dt.hour % 6 == 0:
            selected.append("3month")
        if snapshot_dt.hour == 0:
            selected.append("year")
    return tuple(selected)

def _write_incremental_marker(meta: dict[str, Any]) -> None:
    marker = dict(meta)
    marker["updated_at"] = _utc_now().isoformat()
    _write_json_atomic(_marker_path(), marker)
    latest_log_path = _logs_root() / "graph_cache_incremental_latest.json"
    _write_json_atomic(latest_log_path, marker)


def update_graph_cache_from_snapshot(
    items: list[dict[str, Any]],
    snapshot_bucket: str,
    *,
    windows: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Append one new snapshot point to graph cache files.

    This is intentionally O(new snapshot rows). It does not read JSONL history,
    does not call get_item_history_payload(), and does not rebuild all history.
    """
    started = time.perf_counter()
    snapshot_dt = _parse_ts(snapshot_bucket)
    if snapshot_dt is None:
        raise ValueError(f"invalid snapshot_bucket={snapshot_bucket!r}")

    selected_windows = windows or _windows_for_snapshot_bucket(snapshot_dt)
    hot_item_ids = _load_hot_item_ids()
    ordered_items, stale_priority_count, starved_priority_count, hard_max_stale_count, self_heal_count = _prioritize_graph_items(items, hot_item_ids, selected_windows)
    now_ts = _utc_now().timestamp()
    bypass_self_heal_attempted, bypass_self_heal_repaired, bypass_self_heal_ids, bypass_self_heal_meta = _run_self_heal_bypass_lane(items, selected_windows, snapshot_bucket)

    updated_items = 0
    updated_files = 0
    budget_exhausted = False
    failures: list[dict[str, Any]] = []

    for item in ordered_items:
        if GRAPH_WRITE_BUDGET > 0 and updated_files >= GRAPH_WRITE_BUDGET:
            budget_exhausted = True
            break
        point = _point_from_item(item, snapshot_bucket)
        if point is None:
            continue
        item_id = _to_int(item.get("id"))
        item_updated = False
        for window in selected_windows:
            if GRAPH_WRITE_BUDGET > 0 and updated_files >= GRAPH_WRITE_BUDGET:
                budget_exhausted = True
                break
            if window not in WINDOWS:
                continue
            try:
                force_hard_stale_append = (
                    _graph_cache_window_age_seconds(item_id, window, now_ts)
                    >= HARD_MAX_STALE_GRAPH_SECONDS
                )
                if _update_window_cache(
                    item_id,
                    point,
                    snapshot_dt,
                    window,
                    force_append_unchanged=force_hard_stale_append,
                ):
                    updated_files += 1
                    item_updated = True
            except Exception as exc:
                failures.append({"item_id": item_id, "window": window, "error": str(exc)})
        if item_updated:
            updated_items += 1

    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    meta = {
        "snapshot_bucket": snapshot_bucket,
        "updated_items": updated_items,
        "updated_files": updated_files,
        "graph_write_budget": GRAPH_WRITE_BUDGET,
        "budget_exhausted": budget_exhausted,
        "hot_item_priority_count": len(hot_item_ids),
        "stale_graph_priority_count": stale_priority_count,
        "stale_graph_priority_seconds": STALE_GRAPH_PRIORITY_SECONDS,
        "starved_graph_priority_count": starved_priority_count,
        "starved_graph_priority_seconds": STARVED_GRAPH_PRIORITY_SECONDS,
        "hard_max_stale_graph_count": hard_max_stale_count,
        "hard_max_stale_graph_seconds": HARD_MAX_STALE_GRAPH_SECONDS,
        "self_heal_graph_count": self_heal_count,
        "self_heal_day_min_points": SELF_HEAL_DAY_MIN_POINTS,
        "self_heal_bypass_budget": SELF_HEAL_BYPASS_BUDGET,
        "self_heal_bypass_attempted": bypass_self_heal_attempted,
        "self_heal_bypass_repaired": bypass_self_heal_repaired,
        "self_heal_bypass_item_ids": bypass_self_heal_ids[:25],
        "self_heal_bypass_meta": bypass_self_heal_meta,
        "failures": len(failures),
        "failure_samples": failures[:10],
        "elapsed_ms": elapsed_ms,
        "cache_root": str(_cache_root()),
        "mode": "incremental_append_trim_throttled",
        "throttle_mode": THROTTLE_MODE,
        "updated_windows": list(selected_windows),
    }
    _write_incremental_marker(meta)
    print(
        f"[graph_cache_updater] bucket={snapshot_bucket} "
        f"windows={','.join(selected_windows)} "
        f"items={updated_items} files={updated_files} budget={GRAPH_WRITE_BUDGET} "
        f"budget_exhausted={budget_exhausted} hot_items={len(hot_item_ids)} "
        f"stale_items={stale_priority_count} starved_items={starved_priority_count} "
        f"hard_max_stale_items={hard_max_stale_count} "
        f"self_heal_items={self_heal_count} "
        f"self_heal_bypass_attempted={bypass_self_heal_attempted} "
        f"self_heal_bypass_repaired={bypass_self_heal_repaired} "
        f"self_heal_bypass_rows={bypass_self_heal_meta.get('batch_rows_scanned', 0)} "
        f"self_heal_bypass_ms={bypass_self_heal_meta.get('batch_elapsed_ms', 0)} "
        f"failures={len(failures)} elapsed_ms={elapsed_ms}",
        flush=True,
    )
    return meta


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Incrementally update graph cache from current market cache items")
    parser.add_argument("--snapshot-bucket", default=None, help="ISO snapshot bucket to stamp onto points")
    parser.add_argument("--once-from-market-cache", action="store_true", help="Test update using cache/market_cache.json items")
    args = parser.parse_args(argv)

    if not args.once_from_market_cache:
        raise SystemExit("Use --once-from-market-cache for manual testing.")

    market_cache_path = _data_root() / "cache" / "market_cache.json"
    payload = _read_payload(market_cache_path)
    items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []
    bucket = args.snapshot_bucket or payload.get("snapshot_bucket") or _utc_now().replace(second=0, microsecond=0).isoformat()
    print(json.dumps(
            update_graph_cache_from_snapshot(
                items,
                str(bucket),
                windows=list(WINDOWS) if args.once_from_market_cache else None,
            ),
            indent=2,
        ))


if __name__ == "__main__":
    main()
