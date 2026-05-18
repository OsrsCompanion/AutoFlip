from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from app.services.market_history import HISTORY_GRAPH_CACHE_DIR
except Exception:
    env_root = os.getenv("OSRS_FLIP_DATA_ROOT") or "/mnt/nvme/autoflip-data"
    HISTORY_GRAPH_CACHE_DIR = str(Path(env_root) / "cache" / "history_graphs")

WINDOWS = ("day", "week", "month", "3month", "year")
DEFAULT_INTERVAL_SECONDS = int(os.getenv("OSRS_CACHE_FRESHNESS_INTERVAL_SECONDS", "300"))
DEFAULT_STALE_MINUTES = int(os.getenv("OSRS_CACHE_FRESHNESS_STALE_MINUTES", "15"))
DEFAULT_MAX_PAYLOAD_FILES = int(os.getenv("OSRS_CACHE_FRESHNESS_MAX_PAYLOAD_FILES", "50"))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _safe_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    tmp.replace(path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def _latest_payload_point_ts(path: Path) -> datetime | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        points = payload.get("points") if isinstance(payload, dict) else None
        if not isinstance(points, list) or not points:
            return None
        for point in reversed(points):
            if isinstance(point, dict):
                ts = _parse_ts(point.get("snapshot_ts"))
                if ts:
                    return ts
    except Exception:
        return None
    return None


def _file_info(path: Path, now: datetime, include_payload_ts: bool) -> dict[str, Any]:
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    payload_ts = _latest_payload_point_ts(path) if include_payload_ts else None
    freshness_reference = payload_ts or mtime
    age_seconds = max(0.0, (now - freshness_reference).total_seconds())
    return {
        "path": str(path),
        "item_id": path.stem,
        "size_bytes": stat.st_size,
        "mtime": _iso(mtime),
        "latest_point_ts": _iso(payload_ts),
        "freshness_reference": "latest_point_ts" if payload_ts else "mtime",
        "age_seconds": int(age_seconds),
        "age_minutes": round(age_seconds / 60.0, 2),
    }


def _scan_window(
    root: Path,
    window: str,
    now: datetime,
    stale_seconds: int,
    mode: str,
    max_payload_files: int,
) -> dict[str, Any]:
    window_dir = root / window
    files = sorted(window_dir.glob("*.json")) if window_dir.is_dir() else []
    include_payload_all = mode == "payload"
    include_payload_sample = mode == "both"

    infos: list[dict[str, Any]] = []
    payload_checked = 0

    # Check oldest files first when payload inspection is bounded.
    by_mtime = sorted(files, key=lambda p: p.stat().st_mtime)
    payload_paths = set()
    if include_payload_all:
        payload_paths = set(files)
    elif include_payload_sample and max_payload_files > 0:
        payload_paths = set(by_mtime[:max_payload_files])

    for path in files:
        include_payload_ts = path in payload_paths
        if include_payload_ts:
            payload_checked += 1
        try:
            infos.append(_file_info(path, now, include_payload_ts=include_payload_ts))
        except Exception as exc:
            infos.append({"path": str(path), "item_id": path.stem, "error": str(exc)})

    valid = [info for info in infos if "age_seconds" in info]
    stale = [info for info in valid if int(info["age_seconds"]) > stale_seconds]
    newest = min(valid, key=lambda x: int(x["age_seconds"])) if valid else None
    oldest = max(valid, key=lambda x: int(x["age_seconds"])) if valid else None

    return {
        "window": window,
        "dir": str(window_dir),
        "file_count": len(files),
        "payload_files_checked": payload_checked,
        "stale_count": len(stale),
        "stale_ratio": round((len(stale) / len(valid)) if valid else 0.0, 4),
        "newest": newest,
        "oldest": oldest,
        "oldest_10": sorted(valid, key=lambda x: int(x["age_seconds"]), reverse=True)[:10],
    }


def scan_graph_cache_freshness(
    cache_root: str | Path = HISTORY_GRAPH_CACHE_DIR,
    stale_minutes: int = DEFAULT_STALE_MINUTES,
    mode: str = "mtime",
    max_payload_files: int = DEFAULT_MAX_PAYLOAD_FILES,
) -> dict[str, Any]:
    now = _utc_now()
    root = Path(cache_root)
    stale_seconds = int(stale_minutes * 60)
    windows = [
        _scan_window(
            root=root,
            window=window,
            now=now,
            stale_seconds=stale_seconds,
            mode=mode,
            max_payload_files=max_payload_files,
        )
        for window in WINDOWS
    ]
    total_files = sum(int(window.get("file_count", 0)) for window in windows)
    total_stale = sum(int(window.get("stale_count", 0)) for window in windows)
    stale_ratio = round((total_stale / total_files) if total_files else 0.0, 4)
    stale_pct = round(stale_ratio * 100.0, 2)
    status = "ok" if total_files and total_stale == 0 else "stale" if total_files else "missing"
    return {
        "checked_at": _iso(now),
        "status": status,
        "cache_root": str(root),
        "stale_minutes": stale_minutes,
        "mode": mode,
        "max_payload_files": max_payload_files,
        "total_files": total_files,
        "total_stale": total_stale,
        "stale_ratio": stale_ratio,
        "stale_pct": stale_pct,
        "control_panel_label": f"Stale: {stale_pct:.1f}%",
        "windows": windows,
    }


def _print_summary(payload: dict[str, Any]) -> None:
    print(
        f"[cache_freshness] status={payload.get('status')} "
        f"files={payload.get('total_files')} stale={payload.get('total_stale')} "
        f"stale_ratio={payload.get('stale_ratio')} stale_pct={payload.get('stale_pct')} "
        f"mode={payload.get('mode')} checked_at={payload.get('checked_at')}",
        flush=True,
    )
    for window in payload.get("windows", []):
        oldest = window.get("oldest") or {}
        print(
            f"[cache_freshness] window={window.get('window')} "
            f"files={window.get('file_count')} stale={window.get('stale_count')} "
            f"payload_checked={window.get('payload_files_checked')} "
            f"oldest_age_min={oldest.get('age_minutes')} "
            f"oldest_item={oldest.get('item_id')}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Monitor graph cache freshness without modifying cache files.")
    parser.add_argument("--cache-root", default=HISTORY_GRAPH_CACHE_DIR)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES)
    parser.add_argument("--mode", choices=("mtime", "both", "payload"), default=os.getenv("OSRS_CACHE_FRESHNESS_MODE", "mtime"))
    parser.add_argument("--max-payload-files", type=int, default=DEFAULT_MAX_PAYLOAD_FILES)
    parser.add_argument("--latest-out", default=os.getenv("OSRS_CACHE_FRESHNESS_LATEST_OUT", "/mnt/nvme/autoflip-data/logs/graph_cache_freshness_latest.json"))
    parser.add_argument("--jsonl-out", default=os.getenv("OSRS_CACHE_FRESHNESS_JSONL_OUT", "/mnt/nvme/autoflip-data/logs/graph_cache_freshness.jsonl"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    print("[cache_freshness] starting graph cache freshness monitor", flush=True)
    print(f"[cache_freshness] cache_root={args.cache_root}", flush=True)
    print(f"[cache_freshness] interval={args.interval}s stale_minutes={args.stale_minutes} mode={args.mode}", flush=True)

    while True:
        try:
            payload = scan_graph_cache_freshness(
                cache_root=args.cache_root,
                stale_minutes=args.stale_minutes,
                mode=args.mode,
                max_payload_files=args.max_payload_files,
            )
            _safe_write_json(Path(args.latest_out), payload)
            _append_jsonl(Path(args.jsonl_out), payload)
            _print_summary(payload)
        except KeyboardInterrupt:
            print("[cache_freshness] stopped by keyboard interrupt", flush=True)
            raise
        except Exception as exc:
            print(f"[cache_freshness] ERROR {exc}", flush=True)

        if args.once:
            return
        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    main()
