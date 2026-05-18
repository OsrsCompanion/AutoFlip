from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOCK_PATH = Path(os.getenv("OSRS_GRAPH_CATCHUP_LOCK", "/tmp/autoflip_graph_catchup_worker.lock"))
DEFAULT_DATA_ROOT = Path(os.getenv("OSRS_FLIP_DATA_ROOT", "/mnt/nvme/autoflip-data"))
DEFAULT_MARKET_CACHE = DEFAULT_DATA_ROOT / "cache" / "market_cache.json"
DEFAULT_PROGRESS_PATH = DEFAULT_DATA_ROOT / "cache" / "history_graphs" / "catchup_worker_latest.json"


def _read_temp_c() -> float | None:
    try:
        import subprocess

        out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True, timeout=5).strip()
        # temp=76.8'C
        value = out.split("=")[-1].split("'")[0]
        return float(value)
    except Exception:
        return None


def _utc_bucket() -> str:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    minute = (now.minute // 5) * 5
    return now.replace(minute=minute).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _load_market_items(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError(f"market cache missing items list: {path}")
    return [item for item in items if isinstance(item, dict)]


def _acquire_lock() -> None:
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"catchup worker already appears to be running: {LOCK_PATH}")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    temp = _read_temp_c()
    if temp is not None and temp >= args.pause_temp:
        return {
            "status": "paused_temp_high",
            "temp_c": temp,
            "pause_temp_c": args.pause_temp,
            "repaired": 0,
            "attempted": 0,
        }

    # Import after startup so callers can override env before launching the worker.
    from app.services import graph_cache_updater as gcu

    gcu.SELF_HEAL_BYPASS_BUDGET = max(0, int(args.batch_size))
    gcu.SELF_HEAL_BYPASS_INTERVAL_SECONDS = 0

    items = _load_market_items(Path(args.market_cache))
    bucket = _utc_bucket()
    started = time.perf_counter()
    attempted, repaired, repaired_ids, meta = gcu._run_self_heal_bypass_lane(items, ("day",), bucket)  # noqa: SLF001
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    temp_after = _read_temp_c()

    result = {
        "status": "ok",
        "started_at": datetime.now(UTC).isoformat(),
        "snapshot_bucket": bucket,
        "attempted": attempted,
        "repaired": repaired,
        "repaired_ids": repaired_ids[:25],
        "elapsed_ms": elapsed_ms,
        "temp_c_before": temp,
        "temp_c_after": temp_after,
        "batch_size": args.batch_size,
        "sleep_seconds": args.sleep_seconds,
        "meta": meta,
    }
    _write_json_atomic(Path(args.progress_path), result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely catch up sparse OSRS graph caches outside the live collector.")
    parser.add_argument("--once", action="store_true", help="Run a single guarded repair batch and exit.")
    parser.add_argument("--loop", action="store_true", help="Run guarded repair batches until stopped.")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("OSRS_GRAPH_CATCHUP_BATCH_SIZE", "10")))
    parser.add_argument("--sleep-seconds", type=int, default=int(os.getenv("OSRS_GRAPH_CATCHUP_SLEEP_SECONDS", "600")))
    parser.add_argument("--pause-temp", type=float, default=float(os.getenv("OSRS_GRAPH_CATCHUP_PAUSE_TEMP_C", "75")))
    parser.add_argument("--stop-temp", type=float, default=float(os.getenv("OSRS_GRAPH_CATCHUP_STOP_TEMP_C", "80")))
    parser.add_argument("--market-cache", default=str(DEFAULT_MARKET_CACHE))
    parser.add_argument("--progress-path", default=str(DEFAULT_PROGRESS_PATH))
    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        parser.error("choose --once or --loop")

    _acquire_lock()
    try:
        while True:
            temp = _read_temp_c()
            if temp is not None and temp >= args.stop_temp:
                result = {"status": "stopped_temp_high", "temp_c": temp, "stop_temp_c": args.stop_temp}
                _write_json_atomic(Path(args.progress_path), result)
                print(json.dumps(result, sort_keys=True), flush=True)
                return 2

            result = run_once(args)
            print(json.dumps(result, sort_keys=True), flush=True)

            if args.once:
                return 0
            time.sleep(max(30, int(args.sleep_seconds)))
    finally:
        _release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
