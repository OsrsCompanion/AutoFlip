from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(UTC).isoformat()


def ensure_dirs(root: Path) -> None:
    for subdir in (
        "raw/prices",
        "normalized/prices",
        "metadata",
        "logs",
    ):
        (root / subdir).mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    return count


def raw_snapshot_path(root: Path, snapshot_dt: datetime, server: str, chunk_index: int) -> Path:
    day = snapshot_dt.strftime("%Y-%m-%d")
    stamp = snapshot_dt.strftime("%H%M%S")
    return root / "raw" / "prices" / day / f"{stamp}_{server}_chunk_{chunk_index:03d}.json"


def normalized_jsonl_path(root: Path, snapshot_dt: datetime) -> Path:
    day = snapshot_dt.strftime("%Y-%m-%d")
    return root / "normalized" / "prices" / f"{day}.jsonl"


def latest_log_path(root: Path) -> Path:
    return root / "logs" / "albion_collector_latest.json"
