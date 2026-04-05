from __future__ import annotations

import json
import os
import time
from pathlib import Path
from collections import defaultdict
from urllib.error import URLError
from urllib.request import Request, urlopen
from datetime import UTC, datetime, timedelta
from typing import Any

APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR.parent


def _candidate_data_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("OSRS_FLIP_DATA_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser().resolve())
    roots.extend([
        (APP_DIR / "data").resolve(),
        (BACKEND_DIR / "data").resolve(),
    ])
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _data_root_score(root: Path) -> int:
    history_root = root / "market_history"
    score = 0
    if history_root.exists():
        score += 2
    for rel in (
        "market_history/current_snapshot.json",
        "market_history/tracked_items.json",
        "cache/market_cache.json",
        "cache/high_alch_cache.json",
    ):
        if (root / rel).exists():
            score += 4
    for rel in (
        "market_history/snapshots",
        "market_history/archive/month",
        "market_history/events",
    ):
        path = root / rel
        if path.exists():
            score += 2
            try:
                score += min(20, sum(1 for _ in path.iterdir()))
            except Exception:
                pass
    return score


def _resolve_data_root() -> Path:
    candidates = _candidate_data_roots()
    scored = sorted((( _data_root_score(root), index, root) for index, root in enumerate(candidates)), reverse=True)
    best_score, _idx, best_root = scored[0]
    return best_root if best_score > 0 else candidates[0]


ACTIVE_DATA_ROOT = _resolve_data_root()
DATA_DIR = str((ACTIVE_DATA_ROOT / "market_history").resolve())
SNAPSHOT_DIR = str((Path(DATA_DIR) / "snapshots").resolve())
ARCHIVE_DIR = str((Path(DATA_DIR) / "archive").resolve())
MONTH_ARCHIVE_DIR = str((Path(ARCHIVE_DIR) / "month").resolve())
EVENT_DIR = str((Path(DATA_DIR) / "events").resolve())
TRACKED_ITEMS_PATH = str((Path(DATA_DIR) / "tracked_items.json").resolve())
CURRENT_SNAPSHOT_STATE_PATH = str((Path(DATA_DIR) / "current_snapshot.json").resolve())
CACHE_DIR = str((ACTIVE_DATA_ROOT / "cache").resolve())
CACHE_PATH = str((Path(CACHE_DIR) / "market_cache.json").resolve())
HIGH_ALCH_CACHE_PATH = str((Path(CACHE_DIR) / "high_alch_cache.json").resolve())
MAPPING_CACHE_MAX_AGE_HOURS = 168
MAPPING_ENDPOINT = "https://prices.runescape.wiki/api/v1/osrs/mapping"

RETENTION_DAYS = 31
RAW_RETENTION_DAYS = 7
MIN_TRACKED_VOLUME = 1000
MIN_TRACKED_PRICE = 1_000_000
HOURLY_VOLUME_WINDOW_MINUTES = 60
MONTH_BUCKET_MINUTES = 150  # 2.5 hours -> 288 points over 30 days
TRACK_ALL_GE_ITEMS = True
DEFAULT_TRACKING_REASON = "all_ge_items"


# -----------------------------
# general helpers
# -----------------------------

def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_dirs() -> None:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    os.makedirs(MONTH_ARCHIVE_DIR, exist_ok=True)
    os.makedirs(EVENT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def get_storage_debug_meta() -> dict[str, Any]:
    candidates = [str(root) for root in _candidate_data_roots()]
    return {
        "active_data_root": str(ACTIVE_DATA_ROOT),
        "data_candidates": candidates,
        "snapshot_dir": SNAPSHOT_DIR,
        "event_dir": EVENT_DIR,
        "archive_dir": MONTH_ARCHIVE_DIR,
        "cache_path": CACHE_PATH,
        "cache_exists": os.path.exists(CACHE_PATH),
        "snapshot_file_count": len([name for name in os.listdir(SNAPSHOT_DIR) if name.endswith(".jsonl")]) if os.path.isdir(SNAPSHOT_DIR) else 0,
        "event_file_count": len([name for name in os.listdir(EVENT_DIR) if name.endswith(".jsonl")]) if os.path.isdir(EVENT_DIR) else 0,
        "archive_file_count": len([name for name in os.listdir(MONTH_ARCHIVE_DIR) if name.endswith(".jsonl")]) if os.path.isdir(MONTH_ARCHIVE_DIR) else 0,
    }


def _bucket_for_time(dt: datetime | None = None) -> str:
    current = dt or _utc_now()
    minute = (current.minute // 5) * 5
    return current.replace(minute=minute, second=0, microsecond=0).isoformat()


def _date_path(dt: datetime) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{dt.strftime('%Y-%m-%d')}.jsonl")


def _month_archive_path(dt: datetime) -> str:
    return os.path.join(MONTH_ARCHIVE_DIR, f"{dt.strftime('%Y-%m-%d')}.jsonl")


def _event_path(dt: datetime) -> str:
    return os.path.join(EVENT_DIR, f"{dt.strftime('%Y-%m-%d')}.jsonl")


def _epoch_to_iso(value: Any) -> str | None:
    try:
        raw = int(value or 0)
    except Exception:
        return None
    if raw <= 0:
        return None
    try:
        return datetime.fromtimestamp(raw, tz=UTC).isoformat()
    except Exception:
        return None


def _load_current_snapshot_state() -> dict[str, Any]:
    payload = _read_json(CURRENT_SNAPSHOT_STATE_PATH, {"items": {}, "updated_at": None})
    if not isinstance(payload, dict):
        return {"items": {}, "updated_at": None}
    items = payload.get("items", {})
    if not isinstance(items, dict):
        items = {}
    return {"items": items, "updated_at": payload.get("updated_at")}


def _save_current_snapshot_state(payload: dict[str, Any]) -> None:
    _ensure_dirs()
    _write_json(CURRENT_SNAPSHOT_STATE_PATH, payload)


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _record_from_item(item: dict[str, Any], snapshot_ts: str) -> dict[str, Any]:
    low = _to_int(item.get("low"))
    high = _to_int(item.get("high"))
    item_id = _to_int(item.get("id"))
    high_alch = get_high_alch_value(item_id, item=item, allow_refresh=False)
    low_time = _epoch_to_iso(item.get("low_time"))
    high_time = _epoch_to_iso(item.get("high_time"))
    return {
        "snapshot_ts": snapshot_ts,
        "id": item_id,
        "name": str(item.get("name") or "Unknown item"),
        "low": low,
        "high": high,
        "recent_volume": _to_int(item.get("recent_volume")),
        "low_price_volume": _to_int(item.get("low_price_volume")),
        "high_price_volume": _to_int(item.get("high_price_volume")),
        "low_time": low_time,
        "high_time": high_time,
        "buy_limit": _to_int(item.get("limit")),
        "members": bool(item.get("members", False)),
        "high_alch": high_alch,
    }


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path: str, payload: Any) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp_path, path)


def _iter_jsonl(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _normalize_history_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    item_id = _to_int(row.get("id"))
    if item_id <= 0:
        return None
    snapshot_ts = row.get("snapshot_ts") or _epoch_to_iso(row.get("timestamp")) or _epoch_to_iso(row.get("latest_timestamp"))
    if not snapshot_ts:
        return None
    low = row.get("low")
    high = row.get("high")
    price = row.get("price")
    if low in (None, "") and price not in (None, ""):
        low = price
    if high in (None, "") and price not in (None, ""):
        high = price
    normalized = dict(row)
    normalized["id"] = item_id
    normalized["snapshot_ts"] = snapshot_ts
    normalized["low"] = round(float(low or 0), 3)
    normalized["high"] = round(float(high or 0), 3)
    normalized["recent_volume"] = _to_int(row.get("recent_volume") or row.get("volume") or row.get("trade_volume"))
    normalized["trade_volume"] = _to_int(row.get("trade_volume") or row.get("recent_volume") or row.get("volume"))
    normalized["buy_limit"] = _to_int(row.get("buy_limit") or row.get("limit"))
    normalized["sample_count"] = max(_to_int(row.get("sample_count")), 1)
    normalized["is_compacted"] = bool(row.get("is_compacted", False))
    return normalized


def _jsonl_line_matches_item(line: str, item_id: int) -> bool:
    marker = '"id"'
    pos = line.find(marker)
    if pos < 0:
        return False
    pos = line.find(':', pos + len(marker))
    if pos < 0:
        return False
    pos += 1
    while pos < len(line) and line[pos] in ' 	':
        pos += 1
    end = pos
    while end < len(line) and line[end].isdigit():
        end += 1
    if end == pos:
        return False
    try:
        return int(line[pos:end]) == item_id
    except Exception:
        return False


def _iter_jsonl_for_item(path: str, item_id: int) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    scanned = 0
    matched = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                scanned += 1
                if not _jsonl_line_matches_item(line, item_id):
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                normalized = _normalize_history_row(raw)
                if not normalized:
                    continue
                matched += 1
                rows.append(normalized)
    except FileNotFoundError:
        return rows, scanned, matched
    return rows, scanned, matched


def _iter_history_files(base_dir: str, cutoff: datetime | None = None) -> list[str]:
    if not os.path.isdir(base_dir):
        return []
    files: list[str] = []
    cutoff_date = cutoff.date() if cutoff else None
    for name in sorted(os.listdir(base_dir)):
        if not name.endswith(".jsonl"):
            continue
        include = True
        if cutoff_date is not None:
            try:
                file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC).date()
                include = file_date >= cutoff_date
            except Exception:
                include = True
        if include:
            files.append(os.path.join(base_dir, name))
    return files


def _load_item_rows_from_files(item_id: int, files: list[str], cutoff: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stats = {"files_scanned": 0, "rows_scanned": 0, "rows_matched": 0}
    for path in files:
        file_rows, scanned, matched = _iter_jsonl_for_item(path, item_id)
        stats["files_scanned"] += 1
        stats["rows_scanned"] += scanned
        stats["rows_matched"] += matched
        if cutoff is not None:
            file_rows = [row for row in file_rows if (_parse_ts(row.get("snapshot_ts")) or cutoff) >= cutoff]
        rows.extend(file_rows)
    rows.sort(key=lambda row: row.get("snapshot_ts", ""))
    return rows, stats


def _dedupe_history_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, int]] = set()
    for row in rows:
        key = (str(row.get("snapshot_ts") or ""), _to_int(row.get("id")), int(round(float(row.get("low") or 0))), int(round(float(row.get("high") or 0))))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _load_item_history_window(item_id: int, days: int, include_archive: bool = False, include_events: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = _utc_now() - timedelta(days=days)
    phase_timings_ms: dict[str, int] = {}
    phase_details: dict[str, Any] = {}

    started = time.perf_counter()
    snapshot_files = _iter_history_files(SNAPSHOT_DIR, cutoff=cutoff)
    snapshot_rows, snapshot_stats = _load_item_rows_from_files(item_id, snapshot_files, cutoff=cutoff)
    phase_timings_ms["snapshots_scan"] = int(round((time.perf_counter() - started) * 1000))
    phase_details["snapshots"] = {"files": len(snapshot_files), **snapshot_stats}

    rows = list(snapshot_rows)
    total_files = snapshot_stats["files_scanned"]
    total_scanned = snapshot_stats["rows_scanned"]
    total_matched = snapshot_stats["rows_matched"]

    raw_dir = os.path.join(DATA_DIR, "raw")
    if os.path.isdir(raw_dir):
        started = time.perf_counter()
        raw_files = _iter_history_files(raw_dir, cutoff=cutoff)
        raw_rows, raw_stats = _load_item_rows_from_files(item_id, raw_files, cutoff=cutoff)
        rows.extend(raw_rows)
        total_files += raw_stats["files_scanned"]
        total_scanned += raw_stats["rows_scanned"]
        total_matched += raw_stats["rows_matched"]
        phase_timings_ms["raw_scan"] = int(round((time.perf_counter() - started) * 1000))
        phase_details["raw"] = {"files": len(raw_files), **raw_stats}

    if include_archive:
        started = time.perf_counter()
        archive_files = _iter_history_files(MONTH_ARCHIVE_DIR, cutoff=cutoff)
        archive_rows, archive_stats = _load_item_rows_from_files(item_id, archive_files, cutoff=cutoff)
        rows.extend(archive_rows)
        total_files += archive_stats["files_scanned"]
        total_scanned += archive_stats["rows_scanned"]
        total_matched += archive_stats["rows_matched"]
        phase_timings_ms["archive_scan"] = int(round((time.perf_counter() - started) * 1000))
        phase_details["archive"] = {"files": len(archive_files), **archive_stats}

    if include_events:
        started = time.perf_counter()
        event_files = _iter_history_files(EVENT_DIR, cutoff=cutoff)
        event_rows, event_stats = _load_item_rows_from_files(item_id, event_files, cutoff=cutoff)
        rows.extend(event_rows)
        total_files += event_stats["files_scanned"]
        total_scanned += event_stats["rows_scanned"]
        total_matched += event_stats["rows_matched"]
        phase_timings_ms["events_scan"] = int(round((time.perf_counter() - started) * 1000))
        phase_details["events"] = {"files": len(event_files), **event_stats}

    started = time.perf_counter()
    rows = _dedupe_history_rows(rows)
    phase_timings_ms["dedupe"] = int(round((time.perf_counter() - started) * 1000))

    meta = {
        "active_history_root": DATA_DIR,
        "files_scanned": total_files,
        "rows_scanned": total_scanned,
        "rows_matched": total_matched,
        "phase_timings_ms": phase_timings_ms,
        "phase_details": phase_details,
    }
    return rows, meta


def _append_jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _parse_ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_pct(base: float, current: float) -> float:
    if base <= 0:
        return 0.0
    return round(((base - current) / base) * 100, 3)


def _stability_pct(lows: list[int]) -> float:
    if not lows:
        return 0.0
    avg = _mean([float(v) for v in lows])
    if avg <= 0:
        return 0.0
    return round(((max(lows) - min(lows)) / avg) * 100, 3)


def _bucket_start(dt: datetime, minutes: int) -> datetime:
    base_minute = (dt.hour * 60 + dt.minute) // minutes * minutes
    hour = base_minute // 60
    minute = base_minute % 60
    return dt.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)


def _load_recent_hour_volume_map(reference_time: datetime) -> dict[int, int]:
    """Sum the previous 55 minutes of raw 5-minute volumes.

    The current snapshot's 5-minute volume is added separately so the final
    effective window is one hour (12 x 5-minute periods).
    """
    _ensure_dirs()
    cutoff = reference_time - timedelta(minutes=55)
    totals: dict[int, int] = defaultdict(int)
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        for row in _iter_jsonl(path):
            ts = _parse_ts(row.get("snapshot_ts"))
            if not ts:
                continue
            if ts < cutoff or ts >= reference_time:
                continue
            item_id = _to_int(row.get("id"))
            if item_id <= 0:
                continue
            totals[item_id] += _to_int(row.get("recent_volume"))
    return dict(totals)


def _project_daily_volume(total_volume: int, sample_count: int) -> int:
    if total_volume <= 0 or sample_count <= 0:
        return 0
    return int(round(total_volume * (288 / sample_count)))


# -----------------------------
# high alch lookup cache
# -----------------------------

def load_high_alch_cache() -> dict[str, Any]:
    _ensure_dirs()
    payload = _read_json(HIGH_ALCH_CACHE_PATH, {"updated_at": None, "items": {}})
    if not isinstance(payload, dict):
        return {"updated_at": None, "items": {}}
    items = payload.get("items", {})
    if not isinstance(items, dict):
        items = {}
    return {"updated_at": payload.get("updated_at"), "items": items}


def save_high_alch_cache(payload: dict[str, Any]) -> None:
    _ensure_dirs()
    existing = load_high_alch_cache()
    merged_items = existing.get("items", {}).copy()
    for key, value in (payload.get("items", {}) or {}).items():
        if _to_int(value) > 0:
            merged_items[str(key)] = _to_int(value)
        elif str(key) not in merged_items:
            merged_items[str(key)] = 0
    _write_json(
        HIGH_ALCH_CACHE_PATH,
        {
            "updated_at": payload.get("updated_at") or existing.get("updated_at"),
            "items": merged_items,
        },
    )


def _mapping_cache_is_stale(payload: dict[str, Any], max_age_hours: int = MAPPING_CACHE_MAX_AGE_HOURS) -> bool:
    updated_at = _parse_ts(payload.get("updated_at"))
    if not updated_at:
        return True
    return updated_at < (_utc_now() - timedelta(hours=max_age_hours))


def refresh_high_alch_cache(force: bool = False) -> dict[str, Any]:
    current = load_high_alch_cache()
    if not force and not _mapping_cache_is_stale(current):
        return current

    try:
        request = Request(
            MAPPING_ENDPOINT,
            headers={
                "User-Agent": "OSRS Flip Assistant/1.0 (local tool; contact: local-user)",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return current

    items_map: dict[str, int] = current.get("items", {}).copy()
    if isinstance(parsed, list):
        for row in parsed:
            if not isinstance(row, dict):
                continue
            item_id = _to_int(row.get("id"))
            if item_id <= 0:
                continue
            high_alch = max(
                _to_int(row.get("highalch")),
                _to_int(row.get("high_alch")),
                _to_int(row.get("high_alch_value")),
                _to_int(row.get("highAlchemyValue")),
                _to_int(row.get("value")),
            )
            if high_alch > 0:
                items_map[str(item_id)] = high_alch

    updated = {"updated_at": _utc_now().isoformat(), "items": items_map}
    save_high_alch_cache(updated)
    return load_high_alch_cache()


def get_high_alch_value(item_id: int, item: dict[str, Any] | None = None, allow_refresh: bool = False) -> int:
    if item:
        embedded = max(
            _to_int(item.get("high_alch_value")),
            _to_int(item.get("high_alch")),
            _to_int(item.get("alch_value")),
            _to_int(item.get("high_alchemy_value")),
        )
        if embedded > 0:
            return embedded

    cache = load_high_alch_cache()
    cached = _to_int(cache.get("items", {}).get(str(item_id)))
    if cached > 0:
        return cached

    if allow_refresh:
        refreshed = refresh_high_alch_cache(force=False)
        return _to_int(refreshed.get("items", {}).get(str(item_id)))
    return 0


def seed_high_alch_cache_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    payload = load_high_alch_cache()
    changed = False
    mapped = payload.get("items", {}).copy()
    for item in items:
        item_id = _to_int(item.get("id"))
        if item_id <= 0:
            continue
        embedded = max(
            _to_int(item.get("high_alch_value")),
            _to_int(item.get("high_alch")),
            _to_int(item.get("alch_value")),
            _to_int(item.get("high_alchemy_value")),
        )
        if embedded > 0 and _to_int(mapped.get(str(item_id))) != embedded:
            mapped[str(item_id)] = embedded
            changed = True
    if changed:
        payload = {"updated_at": payload.get("updated_at") or _utc_now().isoformat(), "items": mapped}
        save_high_alch_cache(payload)
        return load_high_alch_cache()
    return payload


# -----------------------------
# tracking universe rules
# -----------------------------

def _load_tracked_items() -> dict[str, Any]:
    _ensure_dirs()
    data = _read_json(TRACKED_ITEMS_PATH, {"tracked_ids": {}, "updated_at": None})
    if not isinstance(data, dict):
        return {"tracked_ids": {}, "updated_at": None}
    tracked_ids = data.get("tracked_ids", {})
    if not isinstance(tracked_ids, dict):
        tracked_ids = {}
    return {"tracked_ids": tracked_ids, "updated_at": data.get("updated_at")}


def _save_tracked_items(payload: dict[str, Any]) -> None:
    _ensure_dirs()
    _write_json(TRACKED_ITEMS_PATH, payload)


def _tracking_reason(item: dict[str, Any], existing_reason: str | None, hourly_volume: int = 0) -> str | None:
    if existing_reason:
        return existing_reason

    low = _to_int(item.get("low"))
    high = _to_int(item.get("high"))
    if low <= 0 or high <= 0:
        return None

    if TRACK_ALL_GE_ITEMS:
        return DEFAULT_TRACKING_REASON

    price = max(low, high)
    if hourly_volume >= MIN_TRACKED_VOLUME:
        return "volume_1h"
    if price >= MIN_TRACKED_PRICE:
        return "high_value"
    return None


def _update_tracking_universe(items: list[dict[str, Any]], snapshot_bucket: str | None = None) -> dict[int, str]:
    payload = _load_tracked_items()
    tracked_ids = payload.get("tracked_ids", {})
    changed = False
    reference_time = _parse_ts(snapshot_bucket) or _utc_now()
    prior_hour_volume = _load_recent_hour_volume_map(reference_time)
    for item in items:
        item_id = _to_int(item.get("id"))
        if item_id <= 0:
            continue
        key = str(item_id)
        hourly_volume = prior_hour_volume.get(item_id, 0) + _to_int(item.get("recent_volume"))
        reason = _tracking_reason(item, tracked_ids.get(key), hourly_volume=hourly_volume)
        if reason and tracked_ids.get(key) != reason:
            tracked_ids[key] = reason
            changed = True
    if changed:
        payload["tracked_ids"] = tracked_ids
        payload["updated_at"] = _utc_now().isoformat()
        _save_tracked_items(payload)
    return {int(item_id): str(reason) for item_id, reason in tracked_ids.items()}


def load_tracked_universe() -> dict[str, Any]:
    payload = _load_tracked_items()
    tracked_ids = payload.get("tracked_ids", {})
    return {
        "tracked_ids": tracked_ids,
        "updated_at": payload.get("updated_at"),
        "tracked_count": len(tracked_ids),
    }


# -----------------------------
# cache helpers
# -----------------------------

def load_market_cache() -> dict[str, Any]:
    _ensure_dirs()
    if not os.path.exists(CACHE_PATH):
        return {"updated_at": None, "snapshot_bucket": None, "items": []}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                data.setdefault("items", [])
                return _repair_market_cache_payload(data, persist=True)
    except Exception:
        pass
    return {"updated_at": None, "snapshot_bucket": None, "items": []}


def save_market_cache(cache: dict[str, Any]) -> None:
    _ensure_dirs()
    _write_json(CACHE_PATH, cache)


def get_market_cache_freshness(max_stale_minutes: int = 15) -> dict[str, Any]:
    cache = load_market_cache()
    updated_at = _parse_ts(cache.get("updated_at"))
    snapshot_bucket = _parse_ts(cache.get("snapshot_bucket"))
    reference_dt = snapshot_bucket or updated_at

    age_seconds = None
    is_stale = True
    status = "missing"
    freshness_label = "Unknown"
    age_display = "-"
    if reference_dt:
        age_seconds = max(0, int((_utc_now() - reference_dt).total_seconds()))
        is_stale = age_seconds > (max_stale_minutes * 60)
        status = "stale" if is_stale else "fresh"

        age_minutes = round(age_seconds / 60, 2)
        if age_minutes < 1:
            age_display = "<1m"
        elif age_minutes < 60:
            age_display = f"{int(age_minutes)}m"
        else:
            age_display = f"{round(age_minutes / 60, 1)}h"

        freshness_label = "Stale" if is_stale else "Fresh"

    return {
        "updated_at": cache.get("updated_at"),
        "snapshot_bucket": cache.get("snapshot_bucket"),
        "cache_item_count": len(cache.get("items", [])) if isinstance(cache, dict) else 0,
        "age_seconds": age_seconds,
        "age_minutes": None if age_seconds is None else round(age_seconds / 60, 2),
        "age_display": age_display,
        "freshness_label": freshness_label,
        "is_stale": is_stale,
        "status": status,
    }


_REQUIRED_CACHE_DETAIL_FIELDS = (
    "day_low",
    "day_high",
    "week_low",
    "week_high",
    "month_low",
    "month_high",
    "avg_day_low",
    "avg_week_low",
    "avg_month_low",
    "dip_vs_day_pct",
    "dip_vs_week_pct",
    "dip_vs_month_pct",
    "stability_day_pct",
    "stability_week_pct",
    "stability_month_pct",
    "buy_limit",
    "high_alch",
    "high_alch_value",
    "history_points",
)


def _cache_item_missing_detail_fields(item: dict[str, Any]) -> bool:
    for key in _REQUIRED_CACHE_DETAIL_FIELDS:
        if key not in item:
            return True
    return False


def _build_history_windows_for_item(history: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    day_cutoff = now - timedelta(days=1)
    week_cutoff = now - timedelta(days=7)

    lows_month = [int(round(float(row.get("low") or 0))) for row in history if float(row.get("low") or 0) > 0]
    highs_month = [int(round(float(row.get("high") or 0))) for row in history if float(row.get("high") or 0) > 0]

    lows_day: list[int] = []
    highs_day: list[int] = []
    lows_week: list[int] = []
    highs_week: list[int] = []
    for row in history:
        ts = _parse_ts(row.get("snapshot_ts"))
        if not ts:
            continue
        low = int(round(float(row.get("low") or 0)))
        high = int(round(float(row.get("high") or 0)))
        if ts >= day_cutoff:
            if low > 0:
                lows_day.append(low)
            if high > 0:
                highs_day.append(high)
        if ts >= week_cutoff:
            if low > 0:
                lows_week.append(low)
            if high > 0:
                highs_week.append(high)

    return {
        "lows_day": lows_day,
        "highs_day": highs_day,
        "lows_week": lows_week,
        "highs_week": highs_week,
        "lows_month": lows_month,
        "highs_month": highs_month,
    }


def _normalize_cached_item(item: dict[str, Any], history: list[dict[str, Any]] | None = None, now: datetime | None = None) -> tuple[dict[str, Any], bool]:
    normalized = dict(item)
    changed = False
    reference_now = now or _utc_now()

    buy_price = _to_int(normalized.get("buy_price") or normalized.get("low"))
    sell_price = _to_int(normalized.get("sell_price") or normalized.get("high"))
    item_id = _to_int(normalized.get("id"))

    windows = _build_history_windows_for_item(history or [], reference_now) if history is not None else None

    day_lows = (windows or {}).get("lows_day", [])
    day_highs = (windows or {}).get("highs_day", [])
    week_lows = (windows or {}).get("lows_week", [])
    week_highs = (windows or {}).get("highs_week", [])
    month_lows = (windows or {}).get("lows_month", [])
    month_highs = (windows or {}).get("highs_month", [])

    defaults: dict[str, Any] = {
        "buy_limit": _to_int(normalized.get("buy_limit")),
        "day_low": min(day_lows) if day_lows else buy_price,
        "day_high": max(day_highs) if day_highs else sell_price,
        "week_low": min(week_lows) if week_lows else buy_price,
        "week_high": max(week_highs) if week_highs else sell_price,
        "month_low": min(month_lows) if month_lows else buy_price,
        "month_high": max(month_highs) if month_highs else sell_price,
        "avg_day_low": round(_mean([float(v) for v in day_lows]), 3) if day_lows else float(buy_price),
        "avg_week_low": round(_mean([float(v) for v in week_lows]), 3) if week_lows else float(buy_price),
        "avg_month_low": round(_mean([float(v) for v in month_lows]), 3) if month_lows else float(buy_price),
        "dip_vs_day_pct": _safe_pct(round(_mean([float(v) for v in day_lows]), 3) if day_lows else float(buy_price), float(buy_price)),
        "dip_vs_week_pct": _safe_pct(round(_mean([float(v) for v in week_lows]), 3) if week_lows else float(buy_price), float(buy_price)),
        "dip_vs_month_pct": _safe_pct(round(_mean([float(v) for v in month_lows]), 3) if month_lows else float(buy_price), float(buy_price)),
        "stability_day_pct": _stability_pct(day_lows),
        "stability_week_pct": _stability_pct(week_lows),
        "stability_month_pct": _stability_pct(month_lows),
        "history_points": len(history or []),
    }

    high_alch = _to_int(normalized.get("high_alch") or normalized.get("high_alch_value"))
    if high_alch <= 0 and history:
        history_values = [_to_int(row.get("high_alch")) for row in history if _to_int(row.get("high_alch")) > 0]
        if history_values:
            high_alch = max(history_values)
    if high_alch <= 0 and item_id > 0:
        high_alch = get_high_alch_value(item_id, allow_refresh=False)
    defaults["high_alch"] = high_alch
    defaults["high_alch_value"] = high_alch

    for key, default_value in defaults.items():
        existing = normalized.get(key)
        missing = existing is None or existing == ""
        if key in {"day_low", "day_high", "week_low", "week_high", "month_low", "month_high", "buy_limit", "high_alch", "high_alch_value", "history_points"}:
            missing = missing or (_to_int(existing) <= 0 and _to_int(default_value) > 0)
        if missing and default_value is not None:
            normalized[key] = default_value
            changed = True

    # hard fallback so the detail card never shows blank ranges
    for low_key, high_key in (("day_low", "day_high"), ("week_low", "week_high"), ("month_low", "month_high")):
        low_value = _to_int(normalized.get(low_key))
        high_value = _to_int(normalized.get(high_key))
        fallback_low = buy_price if buy_price > 0 else sell_price
        fallback_high = sell_price if sell_price > 0 else buy_price
        if low_value <= 0 and fallback_low > 0:
            normalized[low_key] = fallback_low
            changed = True
        if high_value <= 0 and fallback_high > 0:
            normalized[high_key] = fallback_high
            changed = True

    if _to_int(normalized.get("high_alch_value")) != _to_int(normalized.get("high_alch")):
        normalized["high_alch_value"] = _to_int(normalized.get("high_alch"))
        changed = True

    return normalized, changed


def _repair_market_cache_payload(cache: dict[str, Any], persist: bool = True) -> dict[str, Any]:
    if not isinstance(cache, dict):
        return {"updated_at": None, "snapshot_bucket": None, "items": []}
    items = cache.get("items", [])
    if not isinstance(items, list) or not items:
        cache.setdefault("items", [])
        return cache

    items_to_repair = [item for item in items if isinstance(item, dict) and _cache_item_missing_detail_fields(item)]
    if not items_to_repair:
        return cache

    needed_ids = {_to_int(item.get("id")) for item in items_to_repair if _to_int(item.get("id")) > 0}
    history_by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if needed_ids:
        for record in load_history_records(days=RETENTION_DAYS):
            record_id = _to_int(record.get("id"))
            if record_id in needed_ids:
                history_by_item[record_id].append(record)

    now = _utc_now()
    changed_any = False
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = _to_int(item.get("id"))
        normalized_item, changed = _normalize_cached_item(item, history=history_by_item.get(item_id), now=now)
        normalized_items.append(normalized_item)
        changed_any = changed_any or changed

    if changed_any:
        cache = dict(cache)
        cache["items"] = normalized_items
        if persist:
            save_market_cache(cache)
    return cache

# -----------------------------
# intraday trade-event storage
# -----------------------------

def _build_event_rows(
    item: dict[str, Any],
    snapshot_bucket: str,
    previous_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    item_id = _to_int(item.get("id"))
    if item_id <= 0:
        return []

    previous_state = previous_state or {}
    item_name = str(item.get("name") or f"Item {item_id}")
    low = _to_int(item.get("low"))
    high = _to_int(item.get("high"))
    low_time = _epoch_to_iso(item.get("low_time"))
    high_time = _epoch_to_iso(item.get("high_time"))
    low_volume = _to_int(item.get("low_price_volume"))
    high_volume = _to_int(item.get("high_price_volume"))

    candidates: list[dict[str, Any]] = []
    if low > 0 and low_time:
        candidates.append(
            {
                "ts": low_time,
                "side": "buy",
                "price": low,
                "trade_volume": low_volume,
                "low": low,
                "high": high if high > 0 else low,
            }
        )
    if high > 0 and high_time:
        candidates.append(
            {
                "ts": high_time,
                "side": "sell",
                "price": high,
                "trade_volume": high_volume,
                "low": low if low > 0 else high,
                "high": high,
            }
        )

    candidates.sort(key=lambda row: (row.get("ts") or "", 0 if row.get("side") == "buy" else 1))

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        side = str(candidate.get("side") or "")
        prior_price = _to_int(previous_state.get(f"{side}_price"))
        prior_ts = str(previous_state.get(f"{side}_time") or "")
        candidate_price = _to_int(candidate.get("price"))
        candidate_ts = str(candidate.get("ts") or "")
        if candidate_price <= 0 or not candidate_ts:
            continue
        if prior_price == candidate_price and prior_ts == candidate_ts:
            continue
        rows.append(
            {
                "snapshot_ts": candidate_ts,
                "bucket_ts": snapshot_bucket,
                "id": item_id,
                "name": item_name,
                "side": side,
                "price": candidate_price,
                "low": _to_int(candidate.get("low")),
                "high": _to_int(candidate.get("high")),
                "recent_volume": _to_int(item.get("recent_volume")),
                "trade_volume": _to_int(candidate.get("trade_volume")),
                "low_price_volume": low_volume,
                "high_price_volume": high_volume,
                "buy_limit": _to_int(item.get("limit")),
                "members": bool(item.get("members", False)),
                "high_alch": get_high_alch_value(item_id, item=item, allow_refresh=False),
                "source": "latest_trade_event",
            }
        )
        previous_state[f"{side}_price"] = candidate_price
        previous_state[f"{side}_time"] = candidate_ts

    previous_state.update(
        {
            "snapshot_ts": snapshot_bucket,
            "low": low,
            "high": high,
            "buy_price": low,
            "sell_price": high,
            "low_time": low_time,
            "high_time": high_time,
        }
    )
    return rows


def _append_intraday_events(items: list[dict[str, Any]], snapshot_bucket: str, tracked_items: dict[int, str]) -> None:
    if not tracked_items:
        return

    state_payload = _load_current_snapshot_state()
    state_items = state_payload.get("items", {})
    event_rows_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for item in items:
        item_id = _to_int(item.get("id"))
        if item_id <= 0 or item_id not in tracked_items:
            continue
        key = str(item_id)
        previous_state = state_items.get(key, {})
        if not isinstance(previous_state, dict):
            previous_state = {}
        rows = _build_event_rows(item, snapshot_bucket=snapshot_bucket, previous_state=previous_state)
        state_items[key] = previous_state
        for row in rows:
            event_dt = _parse_ts(row.get("snapshot_ts"))
            if not event_dt:
                continue
            event_rows_by_path[_event_path(event_dt)].append(row)

    for event_path, rows in event_rows_by_path.items():
        _append_jsonl(event_path, rows)

    _save_current_snapshot_state({"updated_at": snapshot_bucket, "items": state_items})


# -----------------------------
# raw snapshot storage
# -----------------------------

def append_snapshot(items: list[dict[str, Any]], snapshot_bucket: str | None = None) -> str:
    _ensure_dirs()
    seed_high_alch_cache_from_items(items)
    bucket = snapshot_bucket or _bucket_for_time()
    cache = load_market_cache()
    if cache.get("snapshot_bucket") == bucket:
        return bucket

    tracked_items = _update_tracking_universe(items, snapshot_bucket=bucket)
    dt = datetime.fromisoformat(bucket)
    path = _date_path(dt)
    rows: list[dict[str, Any]] = []
    for item in items:
        item_id = _to_int(item.get("id"))
        if item_id <= 0:
            continue
        if item_id not in tracked_items:
            continue
        rows.append(_record_from_item(item, bucket))
    _append_jsonl(path, rows)
    _append_intraday_events(items, snapshot_bucket=bucket, tracked_items=tracked_items)

    prune_old_snapshots(reference_time=dt)
    compact_old_raw_history(reference_time=dt)
    return bucket


def prune_old_snapshots(reference_time: datetime | None = None) -> None:
    _ensure_dirs()
    cutoff = (reference_time or _utc_now()) - timedelta(days=RETENTION_DAYS)

    for name in os.listdir(SNAPSHOT_DIR):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() < cutoff.date():
            try:
                os.remove(os.path.join(SNAPSHOT_DIR, name))
            except FileNotFoundError:
                pass

    for name in os.listdir(MONTH_ARCHIVE_DIR):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() < cutoff.date():
            try:
                os.remove(os.path.join(MONTH_ARCHIVE_DIR, name))
            except FileNotFoundError:
                pass

    raw_event_cutoff = (reference_time or _utc_now()) - timedelta(days=RAW_RETENTION_DAYS)
    for name in os.listdir(EVENT_DIR):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() < raw_event_cutoff.date():
            try:
                os.remove(os.path.join(EVENT_DIR, name))
            except FileNotFoundError:
                pass


def _aggregate_rows(rows: list[dict[str, Any]], bucket_minutes: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts = _parse_ts(row.get("snapshot_ts"))
        item_id = _to_int(row.get("id"))
        if not ts or item_id <= 0:
            continue
        bucket_ts = _bucket_start(ts, bucket_minutes).isoformat()
        grouped[(item_id, bucket_ts)].append(row)

    aggregated: list[dict[str, Any]] = []
    for (_, bucket_ts), group in grouped.items():
        lows = [_to_int(entry.get("low")) for entry in group if _to_int(entry.get("low")) > 0]
        highs = [_to_int(entry.get("high")) for entry in group if _to_int(entry.get("high")) > 0]
        volumes = [_to_int(entry.get("recent_volume")) for entry in group]
        if not lows or not highs:
            continue
        first = group[0]
        aggregated.append(
            {
                "snapshot_ts": bucket_ts,
                "id": _to_int(first.get("id")),
                "name": str(first.get("name") or "Unknown item"),
                "low": round(_mean([float(v) for v in lows]), 3),
                "high": round(_mean([float(v) for v in highs]), 3),
                "recent_volume": sum(volumes),
                "buy_limit": _to_int(first.get("buy_limit")),
                "members": bool(first.get("members", False)),
                "high_alch": _to_int(first.get("high_alch")),
                "sample_count": len(group),
                "min_low": min(lows),
                "max_low": max(lows),
                "min_high": min(highs),
                "max_high": max(highs),
                "bucket_minutes": bucket_minutes,
                "is_compacted": True,
            }
        )
    aggregated.sort(key=lambda row: (row.get("snapshot_ts", ""), row.get("id", 0)))
    return aggregated


def compact_old_raw_history(reference_time: datetime | None = None) -> None:
    _ensure_dirs()
    reference = reference_time or _utc_now()
    raw_cutoff = reference - timedelta(days=RAW_RETENTION_DAYS)

    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() >= raw_cutoff.date():
            continue

        raw_path = os.path.join(SNAPSHOT_DIR, name)
        rows = _iter_jsonl(raw_path)
        if not rows:
            try:
                os.remove(raw_path)
            except FileNotFoundError:
                pass
            continue

        aggregated = _aggregate_rows(rows, MONTH_BUCKET_MINUTES)
        archive_path = _month_archive_path(file_date)
        if aggregated:
            with open(archive_path, "w", encoding="utf-8") as handle:
                for row in aggregated:
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        try:
            os.remove(raw_path)
        except FileNotFoundError:
            pass


# -----------------------------
# loading mixed history
# -----------------------------

def _load_raw_history(days: int) -> list[dict[str, Any]]:
    _ensure_dirs()
    cutoff = _utc_now() - timedelta(days=days)
    records: list[dict[str, Any]] = []
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() < cutoff.date():
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        for record in _iter_jsonl(path):
            ts = _parse_ts(record.get("snapshot_ts"))
            if not ts or ts < cutoff:
                continue
            records.append(record)
    return records


def _load_event_history(days: int) -> list[dict[str, Any]]:
    _ensure_dirs()
    cutoff = _utc_now() - timedelta(days=days)
    records: list[dict[str, Any]] = []
    for name in sorted(os.listdir(EVENT_DIR)):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() < cutoff.date():
            continue
        event_path = os.path.join(EVENT_DIR, name)
        for record in _iter_jsonl(event_path):
            ts = _parse_ts(record.get("snapshot_ts"))
            if not ts or ts < cutoff:
                continue
            records.append(record)
    return records


def _load_month_archive(days: int) -> list[dict[str, Any]]:
    _ensure_dirs()
    cutoff = _utc_now() - timedelta(days=days)
    records: list[dict[str, Any]] = []
    for name in sorted(os.listdir(MONTH_ARCHIVE_DIR)):
        if not name.endswith(".jsonl"):
            continue
        try:
            file_date = datetime.strptime(name[:-6], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            continue
        if file_date.date() < cutoff.date():
            continue
        path = os.path.join(MONTH_ARCHIVE_DIR, name)
        for record in _iter_jsonl(path):
            ts = _parse_ts(record.get("snapshot_ts"))
            if not ts or ts < cutoff:
                continue
            records.append(record)
    return records


def load_history_records(days: int = RETENTION_DAYS) -> list[dict[str, Any]]:
    reference = _utc_now()
    raw_cutoff = reference - timedelta(days=RAW_RETENTION_DAYS)
    overall_cutoff = reference - timedelta(days=days)

    raw_records = _load_raw_history(days=min(days, RAW_RETENTION_DAYS))
    archive_records: list[dict[str, Any]] = []
    if days > RAW_RETENTION_DAYS and overall_cutoff < raw_cutoff:
        archive_records = _load_month_archive(days=days)

    merged = archive_records + raw_records
    merged.sort(key=lambda row: row.get("snapshot_ts", ""))
    return merged


# -----------------------------
# cache + analytics
# -----------------------------

def build_market_cache(items: list[dict[str, Any]], snapshot_bucket: str | None = None) -> dict[str, Any]:
    bucket = snapshot_bucket or _bucket_for_time()
    seed_high_alch_cache_from_items(items)
    tracked_payload = _load_tracked_items()
    tracked_items_map = tracked_payload.get("tracked_ids", {})
    missing_high_alch_ids = [
        _to_int(item.get("id"))
        for item in items
        if _to_int(item.get("id")) > 0
        and str(_to_int(item.get("id"))) in tracked_items_map
        and get_high_alch_value(_to_int(item.get("id")), item=item, allow_refresh=False) <= 0
    ]
    if missing_high_alch_ids:
        refresh_high_alch_cache(force=False)
    records = load_history_records(days=RETENTION_DAYS)
    by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        item_id = _to_int(record.get("id"))
        if item_id > 0:
            by_item[item_id].append(record)

    tracked_items = tracked_items_map
    now = datetime.fromisoformat(bucket)
    hour_cutoff = now - timedelta(hours=1)
    day_cutoff = now - timedelta(days=1)
    week_cutoff = now - timedelta(days=7)

    cached_items: list[dict[str, Any]] = []
    for item in items:
        item_id = _to_int(item.get("id"))
        if item_id <= 0 or str(item_id) not in tracked_items:
            continue

        history = sorted(by_item.get(item_id, []), key=lambda row: row.get("snapshot_ts", ""))
        lows_month = [int(round(float(row.get("low") or 0))) for row in history if float(row.get("low") or 0) > 0]
        highs_month = [int(round(float(row.get("high") or 0))) for row in history if float(row.get("high") or 0) > 0]

        lows_day: list[int] = []
        highs_day: list[int] = []
        lows_week: list[int] = []
        highs_week: list[int] = []
        volumes_hour: list[int] = []
        volumes_day: list[int] = []
        volumes_week: list[int] = []
        volumes_month: list[int] = []
        for row in history:
            ts = _parse_ts(row.get("snapshot_ts"))
            if not ts:
                continue
            low = int(round(float(row.get("low") or 0)))
            high = int(round(float(row.get("high") or 0)))
            volume = _to_int(row.get("recent_volume"))
            if ts >= hour_cutoff:
                volumes_hour.append(volume)
            if ts >= day_cutoff:
                volumes_day.append(volume)
                if low > 0:
                    lows_day.append(low)
                if high > 0:
                    highs_day.append(high)
            if ts >= week_cutoff:
                volumes_week.append(volume)
                if low > 0:
                    lows_week.append(low)
                if high > 0:
                    highs_week.append(high)
            volumes_month.append(volume)

        low = _to_int(item.get("low"))
        high = _to_int(item.get("high"))
        snapshot_volume_5m = _to_int(item.get("recent_volume"))
        recent_volume = sum(volumes_hour) if volumes_hour else snapshot_volume_5m
        buy_limit = _to_int(item.get("limit"))
        spread = max(high - low, 0)
        spread_pct = round((spread / low) * 100, 3) if low > 0 else 0.0
        profit_per_item = max(high - low - min(int(high * 0.02), 5_000_000), 0)
        roi_pct = round((profit_per_item / low) * 100, 3) if low > 0 else 0.0

        day_low = min(lows_day) if lows_day else low
        day_high = max(highs_day) if highs_day else high
        week_low = min(lows_week) if lows_week else low
        week_high = max(highs_week) if highs_week else high
        month_low = min(lows_month) if lows_month else low
        month_high = max(highs_month) if highs_month else high
        avg_day_low = round(_mean([float(v) for v in lows_day]), 3) if lows_day else float(low)
        avg_week_low = round(_mean([float(v) for v in lows_week]), 3) if lows_week else float(low)
        avg_month_low = round(_mean([float(v) for v in lows_month]), 3) if lows_month else float(low)

        day_volume = sum(volumes_day)
        week_volume = sum(volumes_week)
        month_volume = sum(volumes_month)
        avg_daily_volume = _project_daily_volume(day_volume, len(volumes_day))
        high_alch = get_high_alch_value(item_id, item=item, allow_refresh=False)
        if high_alch <= 0 and history:
            history_values = [_to_int(row.get("high_alch")) for row in history if _to_int(row.get("high_alch")) > 0]
            if history_values:
                high_alch = max(history_values)

        cached_items.append(
            {
                "id": item_id,
                "name": str(item.get("name") or f"Item {item_id}"),
                "buy_price": low,
                "sell_price": high,
                "recent_volume": recent_volume,
                "snapshot_volume_5m": snapshot_volume_5m,
                "day_volume": day_volume,
                "week_volume": week_volume,
                "month_volume": month_volume,
                "avg_daily_volume": avg_daily_volume,
                "buy_limit": buy_limit,
                "members": bool(item.get("members", False)),
                "spread": spread,
                "spread_pct": spread_pct,
                "profit_per_item": profit_per_item,
                "roi_pct": roi_pct,
                "day_low": day_low,
                "day_high": day_high,
                "week_low": week_low,
                "week_high": week_high,
                "month_low": month_low,
                "month_high": month_high,
                "avg_day_low": avg_day_low,
                "avg_week_low": avg_week_low,
                "avg_month_low": avg_month_low,
                "dip_vs_day_pct": _safe_pct(avg_day_low, float(low)),
                "dip_vs_week_pct": _safe_pct(avg_week_low, float(low)),
                "dip_vs_month_pct": _safe_pct(avg_month_low, float(low)),
                "stability_day_pct": _stability_pct(lows_day),
                "stability_week_pct": _stability_pct(lows_week),
                "stability_month_pct": _stability_pct(lows_month),
                "history_points": len(history),
                "tracking_reason": str(tracked_items.get(str(item_id)) or DEFAULT_TRACKING_REASON),
                "high_alch": high_alch,
                "high_alch_value": high_alch,
                "updated_at": bucket,
            }
        )

    cache = {
        "updated_at": _utc_now().isoformat(),
        "snapshot_bucket": bucket,
        "items": sorted(cached_items, key=lambda item: item.get("name", "").lower()),
        "storage": {
            "raw_retention_days": RAW_RETENTION_DAYS,
            "compacted_bucket_minutes": MONTH_BUCKET_MINUTES,
            "month_points_target": 288,
        },
    }
    cache = _repair_market_cache_payload(cache, persist=False)
    save_market_cache(cache)
    return cache


def ensure_history_and_cache(items: list[dict[str, Any]], snapshot_bucket: str | None = None) -> dict[str, Any]:
    bucket = append_snapshot(items, snapshot_bucket=snapshot_bucket)
    cache = load_market_cache()
    if cache.get("snapshot_bucket") != bucket or not cache.get("items"):
        cache = build_market_cache(items, snapshot_bucket=bucket)
    print(
        f"[market_history] ensure_history_and_cache bucket={bucket} items={len(items)} cache_items={len(cache.get('items', []))} root={ACTIVE_DATA_ROOT}"
    )
    return cache


def search_cache(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    cache = load_market_cache()
    items = cache.get("items", []) if isinstance(cache, dict) else []
    text = (query or "").strip().lower()
    if not text:
        return items[:limit]
    exact = [item for item in items if item.get("name", "").lower() == text]
    starts = [item for item in items if item.get("name", "").lower().startswith(text) and item not in exact]
    contains = [item for item in items if text in item.get("name", "").lower() and item not in exact and item not in starts]
    return (exact + starts + contains)[:limit]


# -----------------------------
# graph data helpers
# -----------------------------

def _normalize_history_point(row: dict[str, Any], point_mode: str = "snapshot") -> dict[str, Any]:
    return {
        "snapshot_ts": row.get("snapshot_ts"),
        "low": round(float(row.get("low") or 0), 3),
        "high": round(float(row.get("high") or 0), 3),
        "recent_volume": _to_int(row.get("recent_volume")),
        "volume": _to_int(row.get("trade_volume") or row.get("recent_volume")),
        "trade_volume": _to_int(row.get("trade_volume")),
        "buy_limit": _to_int(row.get("buy_limit")),
        "sample_count": max(_to_int(row.get("sample_count")), 1),
        "min_low": _to_int(row.get("min_low") or row.get("low")),
        "max_high": _to_int(row.get("max_high") or row.get("high")),
        "is_compacted": bool(row.get("is_compacted", False)),
        "point_mode": point_mode,
        "event_side": row.get("side"),
        "event_price": _to_int(row.get("price")),
        "source": row.get("source") or ("snapshot_5m" if point_mode == "snapshot" else point_mode),
    }


def _build_exact_window_series(rows: list[dict[str, Any]], target_points: int, window_minutes: int, point_mode: str = "snapshot") -> list[dict[str, Any]]:
    if not rows or target_points <= 0 or window_minutes <= 0:
        return []
    ordered = sorted((_normalize_history_row(row) or row for row in rows), key=lambda row: row.get("snapshot_ts", ""))
    valid_rows = [row for row in ordered if _parse_ts(row.get("snapshot_ts"))]
    if not valid_rows:
        return []

    bucket_seconds = window_minutes * 60
    latest_ts = _parse_ts(valid_rows[-1].get("snapshot_ts")) or _utc_now()
    latest_epoch = int(latest_ts.timestamp())
    end_epoch = ((latest_epoch // bucket_seconds) + 1) * bucket_seconds
    start_epoch = end_epoch - (target_points * bucket_seconds)

    bucket_lows: list[list[float]] = [[] for _ in range(target_points)]
    bucket_highs: list[list[float]] = [[] for _ in range(target_points)]
    bucket_volumes = [0 for _ in range(target_points)]
    bucket_trade_volumes = [0 for _ in range(target_points)]
    bucket_limits = [0 for _ in range(target_points)]
    bucket_samples = [0 for _ in range(target_points)]
    bucket_min_low: list[list[int]] = [[] for _ in range(target_points)]
    bucket_max_high: list[list[int]] = [[] for _ in range(target_points)]

    for row in valid_rows:
        ts = _parse_ts(row.get("snapshot_ts"))
        if not ts:
            continue
        index = int((int(ts.timestamp()) - start_epoch) // bucket_seconds)
        if index < 0 or index >= target_points:
            continue
        low = float(row.get("low") or 0)
        high = float(row.get("high") or 0)
        if low > 0:
            bucket_lows[index].append(low)
            bucket_min_low[index].append(int(round(low)))
        if high > 0:
            bucket_highs[index].append(high)
            bucket_max_high[index].append(int(round(high)))
        bucket_volumes[index] += _to_int(row.get("recent_volume") or row.get("volume"))
        bucket_trade_volumes[index] += _to_int(row.get("trade_volume"))
        bucket_limits[index] = max(bucket_limits[index], _to_int(row.get("buy_limit")))
        bucket_samples[index] += max(_to_int(row.get("sample_count")), 1)

    first_low = next((float(row.get("low") or 0) for row in valid_rows if float(row.get("low") or 0) > 0), 0.0)
    first_high = next((float(row.get("high") or 0) for row in valid_rows if float(row.get("high") or 0) > 0), 0.0)
    carry_low = first_low
    carry_high = first_high if first_high > 0 else first_low
    carry_limit = next((_to_int(row.get("buy_limit")) for row in valid_rows if _to_int(row.get("buy_limit")) > 0), 0)

    points: list[dict[str, Any]] = []
    for index in range(target_points):
        bucket_start = datetime.fromtimestamp(start_epoch + (index * bucket_seconds), tz=UTC)
        lows = bucket_lows[index]
        highs = bucket_highs[index]
        if lows:
            carry_low = round(sum(lows) / len(lows), 3)
        if highs:
            carry_high = round(sum(highs) / len(highs), 3)
        if bucket_limits[index] > 0:
            carry_limit = bucket_limits[index]
        low_value = carry_low if carry_low > 0 else 0
        high_value = carry_high if carry_high > 0 else low_value
        points.append({
            "snapshot_ts": bucket_start.isoformat(),
            "low": low_value,
            "high": high_value,
            "recent_volume": bucket_volumes[index],
            "volume": bucket_volumes[index] or bucket_trade_volumes[index],
            "trade_volume": bucket_trade_volumes[index],
            "buy_limit": carry_limit,
            "sample_count": max(bucket_samples[index], 1),
            "min_low": min(bucket_min_low[index]) if bucket_min_low[index] else int(round(low_value)) if low_value > 0 else 0,
            "max_high": max(bucket_max_high[index]) if bucket_max_high[index] else int(round(high_value)) if high_value > 0 else 0,
            "is_compacted": not bool(lows or highs),
            "point_mode": point_mode,
            "source": f"{point_mode}_exact_288",
        })
    return points


def _bucket_history(rows: list[dict[str, Any]], target_points: int, point_mode: str = "snapshot") -> list[dict[str, Any]]:
    if not rows:
        return []
    if len(rows) <= target_points:
        return [_normalize_history_point(row, point_mode=point_mode) for row in rows]

    bucket_size = max(1, len(rows) // target_points)
    if len(rows) % target_points:
        bucket_size += 1

    compacted: list[dict[str, Any]] = []
    for start in range(0, len(rows), bucket_size):
        group = rows[start : start + bucket_size]
        lows = [float(entry.get("low") or 0) for entry in group if float(entry.get("low") or 0) > 0]
        highs = [float(entry.get("high") or 0) for entry in group if float(entry.get("high") or 0) > 0]
        if not lows or not highs:
            continue
        compacted.append(
            {
                "snapshot_ts": group[-1].get("snapshot_ts"),
                "low": round(_mean(lows), 3),
                "high": round(_mean(highs), 3),
                "recent_volume": sum(_to_int(entry.get("recent_volume")) for entry in group),
                "volume": sum(_to_int(entry.get("trade_volume") or entry.get("recent_volume")) for entry in group),
                "trade_volume": sum(_to_int(entry.get("trade_volume")) for entry in group),
                "buy_limit": max(_to_int(entry.get("buy_limit")) for entry in group),
                "sample_count": sum(max(_to_int(entry.get("sample_count")), 1) for entry in group),
                "min_low": min(int(round(value)) for value in lows),
                "max_high": max(int(round(value)) for value in highs),
                "is_compacted": True,
                "point_mode": point_mode,
                "source": f"{point_mode}_bucketed",
            }
        )
    return compacted[:target_points]


def _build_intraday_hybrid_history(item_id: int, target_points: int = 960) -> dict[str, Any]:
    rows, debug_meta = _load_item_history_window(item_id=item_id, days=1, include_archive=False, include_events=True)
    points: list[dict[str, Any]] = []
    for row in rows:
        point_mode = "trade_event" if row.get("side") else "snapshot"
        points.append(_normalize_history_point(row, point_mode=point_mode))

    points.sort(key=lambda row: (str(row.get("snapshot_ts") or ""), 0 if row.get("point_mode") == "snapshot" else 1))
    raw_point_count = len(points)
    if raw_point_count:
        points = _build_exact_window_series(points, target_points=target_points, window_minutes=5, point_mode="intraday_hybrid")

    return {
        "points": points,
        "raw_point_count": raw_point_count,
        "target_points": target_points,
        "point_source": "intraday_event_hybrid",
        "window_strategy": "trade_events_plus_snapshot_anchors",
        "storage_mode": "event+snapshot",
        "contains_trade_events": any(point.get("point_mode") == "trade_event" for point in points),
        **debug_meta,
    }


def get_item_history_payload(item_id: int, window: str = "month") -> dict[str, Any]:
    started = time.perf_counter()
    if window == "day":
        payload = _build_intraday_hybrid_history(item_id=item_id, target_points=288)
        if not payload.get("points"):
            rows, debug_meta = _load_item_history_window(item_id=item_id, days=1, include_archive=False, include_events=False)
            payload = {
                "points": _build_exact_window_series(rows, 288, 5, point_mode="snapshot"),
                "raw_point_count": len(rows),
                "target_points": 288,
                "point_source": "snapshot_5m",
                "window_strategy": "snapshot_only_fallback",
                "storage_mode": "snapshot_only",
                "contains_trade_events": False,
                **debug_meta,
            }
    elif window == "week":
        rows, debug_meta = _load_item_history_window(item_id=item_id, days=7, include_archive=False, include_events=False)
        payload = {
            "points": _build_exact_window_series(rows, 288, 35, point_mode="snapshot"),
            "raw_point_count": len(rows),
            "target_points": 288,
            "point_source": "snapshot_5m",
            "window_strategy": "raw_snapshot_rebucketed",
            "storage_mode": "snapshot_only",
            "contains_trade_events": False,
            **debug_meta,
        }
    else:
        rows, debug_meta = _load_item_history_window(item_id=item_id, days=RETENTION_DAYS, include_archive=True, include_events=False)
        payload = {
            "points": _build_exact_window_series(rows, 288, 150, point_mode="snapshot"),
            "raw_point_count": len(rows),
            "target_points": 288,
            "point_source": "archive_plus_snapshot",
            "window_strategy": "compacted_archive_plus_recent_raw",
            "storage_mode": "archive+snapshot",
            "contains_trade_events": False,
            **debug_meta,
        }

    points = payload.get("points") or []
    timestamps = [point.get("snapshot_ts") for point in points if point.get("snapshot_ts")]
    payload.update(get_storage_debug_meta())
    payload["first_point_ts"] = timestamps[0] if timestamps else None
    payload["last_point_ts"] = timestamps[-1] if timestamps else None
    payload["point_count"] = len(points)
    payload.setdefault("phase_timings_ms", {})["total"] = int(round((time.perf_counter() - started) * 1000))
    print(
        f"[market_history] history item={item_id} window={window} points={len(points)} raw={payload.get('raw_point_count', 0)} files={payload.get('files_scanned', 0)} rows={payload.get('rows_scanned', 0)} matched={payload.get('rows_matched', 0)} total_ms={payload.get('phase_timings_ms', {}).get('total', 0)} root={ACTIVE_DATA_ROOT}"
    )
    return payload


def get_item_history(item_id: int, window: str = "month") -> list[dict[str, Any]]:
    return get_item_history_payload(item_id=item_id, window=window).get("points", [])
