from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

WINDOW_DAYS: dict[str, int] = {"3month": 93, "year": 365}
TARGET_POINTS: dict[str, int] = {"3month": 93, "year": 53}
SUPPORTED_WINDOWS = tuple(WINDOW_DAYS.keys())


def _data_root() -> Path:
    env_root = os.getenv("OSRS_FLIP_DATA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path("/mnt/nvme/autoflip-data").resolve()


def _history_root(data_root: Path) -> Path:
    return data_root / "market_history"


def _cache_root(data_root: Path) -> Path:
    return data_root / "cache" / "history_graphs"


def _logs_root(data_root: Path) -> Path:
    return data_root / "logs"


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


def _history_files(history_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for rel in (
        "raw/*.jsonl",
        "archive/month/*.jsonl",
        "snapshots/*.jsonl",
    ):
        candidates.extend(history_root.glob(rel))
    return sorted(set(candidates), key=lambda p: str(p))


def _date_hint(path: Path) -> datetime | None:
    try:
        stem = path.stem
        return datetime.fromisoformat(stem).replace(tzinfo=UTC)
    except Exception:
        return None


def _bucket_ts(ts: datetime, window: str) -> str:
    if window == "year":
        start = (ts - timedelta(days=ts.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat()
    if window == "3month":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    raise ValueError(f"unsupported window={window!r}")


@dataclass
class BucketAgg:
    snapshot_ts: str
    item_id: int
    low_sum: float = 0.0
    high_sum: float = 0.0
    low_count: int = 0
    high_count: int = 0
    min_low: float | None = None
    max_high: float | None = None
    recent_volume: int = 0
    trade_volume: int = 0
    buy_limit: int = 0
    sample_count: int = 0
    last_point: dict[str, Any] = field(default_factory=dict)

    def add(self, point: dict[str, Any]) -> None:
        low = _to_float(point.get("low") or point.get("buy_price"))
        high = _to_float(point.get("high") or point.get("sell_price"))
        if high <= 0 and low <= 0:
            return
        if high <= 0 < low:
            high = low
        elif low <= 0 < high:
            low = high
        if high < low:
            high, low = low, high

        if low > 0:
            self.low_sum += low
            self.low_count += 1
            self.min_low = low if self.min_low is None else min(self.min_low, low)
        if high > 0:
            self.high_sum += high
            self.high_count += 1
            self.max_high = high if self.max_high is None else max(self.max_high, high)

        recent_volume = _to_int(point.get("recent_volume") or point.get("volume"))
        trade_volume = _to_int(point.get("trade_volume"))
        buy_limit = _to_int(point.get("buy_limit") or point.get("limit"))
        self.recent_volume += recent_volume
        self.trade_volume += trade_volume
        if buy_limit:
            self.buy_limit = buy_limit
        self.sample_count += max(_to_int(point.get("sample_count")), 1)
        self.last_point = dict(point)

    def to_point(self, window: str) -> dict[str, Any] | None:
        if self.low_count <= 0 and self.high_count <= 0:
            return None
        low_avg = self.low_sum / self.low_count if self.low_count else self.high_sum / max(self.high_count, 1)
        high_avg = self.high_sum / self.high_count if self.high_count else self.low_sum / max(self.low_count, 1)
        point = dict(self.last_point)
        point.update(
            {
                "snapshot_ts": self.snapshot_ts,
                "low": round(low_avg, 3),
                "high": round(high_avg, 3),
                "recent_volume": self.recent_volume,
                "volume": self.trade_volume or self.recent_volume,
                "trade_volume": self.trade_volume,
                "buy_limit": self.buy_limit,
                "sample_count": max(self.sample_count, 1),
                "min_low": int(round(self.min_low if self.min_low is not None else low_avg)),
                "max_high": int(round(self.max_high if self.max_high is not None else high_avg)),
                "is_compacted": True,
                "point_mode": f"{window}_backfill_compressed",
                "source": f"{window}_historical_backfill",
            }
        )
        return point


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                yield row


def _window_payload(item_id: int, window: str, points: list[dict[str, Any]], cache_path: Path) -> dict[str, Any]:
    points = sorted(points, key=lambda p: str(p.get("snapshot_ts") or ""))
    target_points = TARGET_POINTS[window]
    if len(points) > target_points:
        points = points[-target_points:]
    timestamps = [str(p.get("snapshot_ts")) for p in points if p.get("snapshot_ts")]
    return {
        "target_points": target_points,
        "point_source": f"{window}_historical_backfill",
        "window_strategy": f"{window}_historical_bucket_backfill",
        "storage_mode": "graph_cache_only",
        "contains_trade_events": False,
        "points": points,
        "raw_point_count": len(points),
        "point_count": len(points),
        "first_point_ts": timestamps[0] if timestamps else None,
        "last_point_ts": timestamps[-1] if timestamps else None,
        "latest_point_ts": timestamps[-1] if timestamps else None,
        "served_from_graph_cache": False,
        "served_from_memory_cache": False,
        "graph_cache_path": str(cache_path),
        "graph_cache_built_at": _utc_now().isoformat(),
        "graph_cache_update_mode": f"{window}_targeted_historical_backfill",
        "item_id": item_id,
    }


def backfill_long_graph_cache(*, windows: tuple[str, ...], dry_run: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    data_root = _data_root()
    history_root = _history_root(data_root)
    cache_root = _cache_root(data_root)
    logs_root = _logs_root(data_root)
    now = _utc_now()
    cutoffs = {window: now - timedelta(days=WINDOW_DAYS[window]) for window in windows}

    # window -> item_id -> bucket_ts -> aggregate
    aggregates: dict[str, dict[int, dict[str, BucketAgg]]] = {window: {} for window in windows}
    files_seen = 0
    rows_seen = 0
    rows_used = 0

    for path in _history_files(history_root):
        hint = _date_hint(path)
        if hint is not None and all(hint < cutoff - timedelta(days=2) for cutoff in cutoffs.values()):
            continue
        files_seen += 1
        for row in _iter_jsonl(path):
            rows_seen += 1
            item_id = _to_int(row.get("id") or row.get("item_id"))
            if item_id <= 0:
                continue
            ts = _parse_ts(row.get("snapshot_ts") or row.get("ts") or row.get("timestamp"))
            if ts is None:
                continue
            used_row = False
            for window in windows:
                if ts < cutoffs[window]:
                    continue
                low = _to_float(row.get("low") or row.get("buy_price"))
                high = _to_float(row.get("high") or row.get("sell_price"))
                if high <= 0 and low <= 0:
                    continue
                bucket = _bucket_ts(ts, window)
                item_buckets = aggregates[window].setdefault(item_id, {})
                agg = item_buckets.get(bucket)
                if agg is None:
                    agg = BucketAgg(snapshot_ts=bucket, item_id=item_id)
                    item_buckets[bucket] = agg
                agg.add(row)
                used_row = True
            if used_row:
                rows_used += 1

    written_files = 0
    item_counts: dict[str, int] = {}
    point_counts: dict[str, int] = {}

    for window, item_map in aggregates.items():
        item_counts[window] = len(item_map)
        point_counts[window] = 0
        for item_id, bucket_map in item_map.items():
            points = [p for agg in bucket_map.values() if (p := agg.to_point(window)) is not None]
            cache_path = cache_root / window / f"{item_id}.json"
            payload = _window_payload(item_id, window, points, cache_path)
            point_counts[window] += len(payload["points"])
            if not dry_run:
                _write_json_atomic(cache_path, payload)
                written_files += 1

    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    meta = {
        "generated_at": _utc_now().isoformat(),
        "active_data_root": str(data_root),
        "history_root": str(history_root),
        "cache_root": str(cache_root),
        "windows": list(windows),
        "dry_run": dry_run,
        "files_seen": files_seen,
        "rows_seen": rows_seen,
        "rows_used": rows_used,
        "items_by_window": item_counts,
        "points_by_window": point_counts,
        "written_files": written_files,
        "elapsed_ms": elapsed_ms,
        "mode": "targeted_long_graph_cache_backfill",
    }
    if not dry_run:
        _write_json_atomic(cache_root / "long_window_backfill_latest.json", meta)
        _write_json_atomic(logs_root / "graph_cache_long_window_backfill_latest.json", meta)
    return meta


def _parse_windows(value: str) -> tuple[str, ...]:
    windows = tuple(part.strip() for part in value.split(",") if part.strip())
    bad = [window for window in windows if window not in SUPPORTED_WINDOWS]
    if bad:
        raise argparse.ArgumentTypeError(f"unsupported windows={bad}; supported={SUPPORTED_WINDOWS}")
    return windows or SUPPORTED_WINDOWS


def main() -> None:
    parser = argparse.ArgumentParser(description="Targeted historical backfill for 3month/year graph cache files.")
    parser.add_argument("--windows", type=_parse_windows, default=SUPPORTED_WINDOWS, help="Comma-separated windows: 3month,year")
    parser.add_argument("--dry-run", action="store_true", help="Scan and summarize without writing cache files")
    args = parser.parse_args()
    meta = backfill_long_graph_cache(windows=args.windows, dry_run=args.dry_run)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
