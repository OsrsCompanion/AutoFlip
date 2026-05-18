from __future__ import annotations

import json
import os
from pathlib import Path


def _data_root() -> Path:
    env_root = os.getenv("OSRS_FLIP_DATA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path("/mnt/nvme/autoflip-data").resolve()


def _sample_window(window: str, limit: int = 30) -> dict[str, object]:
    root = _data_root() / "cache" / "history_graphs" / window
    files = sorted(root.glob("*.json"))[:limit]
    counts: list[int] = []
    first_ts = None
    last_ts = None
    for path in files:
        try:
            payload = json.load(path.open("r", encoding="utf-8"))
        except Exception:
            continue
        points = payload.get("points")
        if not isinstance(points, list):
            continue
        counts.append(len(points))
        if points:
            first_ts = first_ts or points[0].get("snapshot_ts")
            last_ts = points[-1].get("snapshot_ts")
    return {
        "window": window,
        "sample_files": len(files),
        "min_points": min(counts) if counts else 0,
        "max_points": max(counts) if counts else 0,
        "avg_points": round(sum(counts) / len(counts), 2) if counts else 0,
        "first_ts_sample": first_ts,
        "last_ts_sample": last_ts,
    }


def main() -> None:
    results = [_sample_window("3month"), _sample_window("year")]
    print(json.dumps(results, indent=2))
    failures = []
    for result in results:
        if result["sample_files"] <= 0:
            failures.append(f"{result['window']}: no files")
        if result["max_points"] <= 1:
            failures.append(f"{result['window']}: still only one point in sample")
    if failures:
        raise SystemExit("verification failed: " + "; ".join(failures))
    print("long graph cache backfill verification passed")


if __name__ == "__main__":
    main()
