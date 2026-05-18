from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DATA_ROOT = Path(os.getenv("OSRS_FLIP_DATA_ROOT", "/mnt/nvme/autoflip-data")).expanduser().resolve()
WINDOW = "day"
DAY_WINDOW_HOURS = 24


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
            dt = dt.replace(tzinfo=UTC)
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


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _normalize_point(row: dict[str, Any]) -> dict[str, Any]:
    low = _to_float(row.get("low"))
    high = _to_float(row.get("high"))
    recent_volume = _to_int(row.get("recent_volume"))
    buy_limit = _to_int(row.get("buy_limit") or row.get("limit"))
    return {
        "snapshot_ts": row.get("snapshot_ts"),
        "low": low,
        "high": high,
        "recent_volume": recent_volume,
        "volume": recent_volume,
        "trade_volume": recent_volume,
        "buy_limit": buy_limit,
        "sample_count": _to_int(row.get("sample_count")) or 1,
        "min_low": _to_int(row.get("min_low")) or _to_int(low),
        "max_high": _to_int(row.get("max_high")) or _to_int(high),
        "is_compacted": False,
        "point_mode": "snapshot",
        "source": "snapshot_5m",
    }


def _load_rows(snapshot_path: Path, cutoff: datetime | None) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    by_item: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rows_read = 0
    rows_used = 0
    malformed = 0
    first_ts: str | None = None
    last_ts: str | None = None

    with snapshot_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            rows_read += 1
            try:
                row = json.loads(line)
            except Exception:
                malformed += 1
                continue

            ts_text = row.get("snapshot_ts")
            ts = _parse_ts(ts_text)
            if ts is None:
                malformed += 1
                continue
            if cutoff is not None and ts < cutoff:
                continue

            item_id = _to_int(row.get("id"))
            if item_id <= 0:
                malformed += 1
                continue

            point = _normalize_point(row)
            by_item[item_id].append(point)
            rows_used += 1
            if first_ts is None or str(ts_text) < first_ts:
                first_ts = str(ts_text)
            if last_ts is None or str(ts_text) > last_ts:
                last_ts = str(ts_text)

    return by_item, {
        "rows_read": rows_read,
        "rows_used": rows_used,
        "malformed_rows": malformed,
        "items": len(by_item),
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _merge_points(existing: list[dict[str, Any]], repair_points: list[dict[str, Any]], cutoff: datetime | None) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for point in existing:
        ts_text = point.get("snapshot_ts")
        ts = _parse_ts(ts_text)
        if not ts_text or ts is None:
            continue
        if cutoff is not None and ts < cutoff:
            continue
        merged[str(ts_text)] = point

    # Raw snapshot points win over existing points for the same bucket.
    for point in repair_points:
        ts_text = point.get("snapshot_ts")
        ts = _parse_ts(ts_text)
        if not ts_text or ts is None:
            continue
        if cutoff is not None and ts < cutoff:
            continue
        merged[str(ts_text)] = point

    return [merged[key] for key in sorted(merged.keys())]


def repair_day_graph_cache_from_snapshot(
    snapshot_path: str | Path | None = None,
    data_root: str | Path = DEFAULT_DATA_ROOT,
    hours: int = DAY_WINDOW_HOURS,
    dry_run: bool = False,
    max_items: int | None = None,
) -> dict[str, Any]:
    started = _utc_now()
    data_root = Path(data_root).expanduser().resolve()
    if snapshot_path is None:
        snapshot_path = data_root / "market_history" / "snapshots" / f"{started.strftime('%Y-%m-%d')}.jsonl"
    snapshot_path = Path(snapshot_path).expanduser().resolve()
    if not snapshot_path.exists():
        raise FileNotFoundError(f"snapshot file not found: {snapshot_path}")

    cutoff = started - timedelta(hours=max(1, int(hours)))
    graph_day_root = data_root / "cache" / "history_graphs" / WINDOW

    by_item, load_meta = _load_rows(snapshot_path, cutoff=cutoff)
    item_ids = sorted(by_item.keys())
    if max_items is not None and max_items > 0:
        item_ids = item_ids[:max_items]

    written = 0
    unchanged = 0
    failures = 0
    failure_samples: list[dict[str, Any]] = []

    for item_id in item_ids:
        try:
            cache_path = graph_day_root / f"{item_id}.json"
            existing_payload = _read_json(cache_path, {})
            existing_points = existing_payload.get("points", []) if isinstance(existing_payload, dict) else []
            if not isinstance(existing_points, list):
                existing_points = []

            points = _merge_points(existing_points, by_item[item_id], cutoff=cutoff)
            timestamps = [str(point.get("snapshot_ts")) for point in points if point.get("snapshot_ts")]

            payload = dict(existing_payload) if isinstance(existing_payload, dict) else {}
            payload["points"] = points
            payload["window"] = WINDOW
            payload["item_id"] = item_id
            payload["raw_point_count"] = len(points)
            payload["point_count"] = len(points)
            payload["latest_point_ts"] = timestamps[-1] if timestamps else None
            payload["data_freshness_ts"] = timestamps[-1] if timestamps else None
            payload["served_from_graph_cache"] = False
            payload["graph_cache_built_at"] = _utc_now().isoformat()
            payload["graph_cache_path"] = str(cache_path)
            payload["point_source"] = "snapshot_5m"
            payload["window_strategy"] = "day_repaired_from_raw_snapshot_5m"
            payload["storage_mode"] = "snapshot_only"
            payload["repair_source"] = str(snapshot_path)
            payload["repair_updated_at"] = _utc_now().isoformat()

            if dry_run:
                unchanged += 1
            else:
                _write_json(cache_path, payload)
                written += 1
        except Exception as exc:
            failures += 1
            if len(failure_samples) < 10:
                failure_samples.append({"item_id": item_id, "error": str(exc)})

    elapsed_ms = int(round((_utc_now() - started).total_seconds() * 1000))
    meta = {
        "mode": "day_graph_cache_repair_from_raw_snapshot",
        "snapshot_path": str(snapshot_path),
        "data_root": str(data_root),
        "window": WINDOW,
        "hours": hours,
        "cutoff": cutoff.isoformat(),
        "items_loaded": load_meta.get("items", 0),
        "items_considered": len(item_ids),
        "files_written": written,
        "dry_run_items": unchanged,
        "failures": failures,
        "failure_samples": failure_samples,
        "elapsed_ms": elapsed_ms,
        "started_at": started.isoformat(),
        "finished_at": _utc_now().isoformat(),
        "load_meta": load_meta,
    }

    logs_root = data_root / "logs"
    if not dry_run:
        _write_json(logs_root / "day_graph_cache_repair_latest.json", meta)
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="One-time repair for day graph cache from raw 5-minute snapshots")
    parser.add_argument("--snapshot", default=None, help="Snapshot JSONL file to repair from. Defaults to today's runtime snapshot file.")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="Runtime data root. Defaults to OSRS_FLIP_DATA_ROOT or /mnt/nvme/autoflip-data.")
    parser.add_argument("--hours", type=int, default=DAY_WINDOW_HOURS, help="Rolling day window to keep. Default: 24.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without writing cache files.")
    parser.add_argument("--max-items", type=int, default=0, help="Optional item limit for testing.")
    args = parser.parse_args()

    meta = repair_day_graph_cache_from_snapshot(
        snapshot_path=args.snapshot,
        data_root=args.data_root,
        hours=args.hours,
        dry_run=args.dry_run,
        max_items=args.max_items or None,
    )
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
