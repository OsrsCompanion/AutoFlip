from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from .api_client import AlbionApiError, fetch_prices
from .config import AlbionCollectorConfig, default_config
from .storage import (
    append_jsonl,
    ensure_dirs,
    iso_utc,
    latest_log_path,
    normalized_jsonl_path,
    raw_snapshot_path,
    utc_now,
    write_json_atomic,
)


def _chunks(values: tuple[str, ...], size: int) -> list[tuple[str, ...]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _normalize_price_row(row: dict[str, Any], *, server: str, snapshot_ts: str) -> dict[str, Any]:
    sell_price_min = _to_int(row.get("sell_price_min"))
    buy_price_max = _to_int(row.get("buy_price_max"))
    return {
        "game": "albion",
        "server": server,
        "snapshot_ts": snapshot_ts,
        "item_id": str(row.get("item_id") or ""),
        "location": str(row.get("city") or row.get("location") or ""),
        "quality": _to_int(row.get("quality")),
        "sell_price_min": sell_price_min,
        "sell_price_min_date": row.get("sell_price_min_date") or None,
        "sell_price_max": _to_int(row.get("sell_price_max")),
        "sell_price_max_date": row.get("sell_price_max_date") or None,
        "buy_price_min": _to_int(row.get("buy_price_min")),
        "buy_price_min_date": row.get("buy_price_min_date") or None,
        "buy_price_max": buy_price_max,
        "buy_price_max_date": row.get("buy_price_max_date") or None,
        "observed_spread": sell_price_min - buy_price_max if sell_price_min and buy_price_max else 0,
        "source": "albion_online_data_project",
    }


def collect_once(config: AlbionCollectorConfig | None = None) -> dict[str, Any]:
    """Collect one Albion price snapshot pass.

    This is intentionally isolated and raw-data-first. It does not build graphs,
    recommendations, or derived caches.
    """
    config = config or default_config()
    root = config.albion_root
    ensure_dirs(root)

    started = time.perf_counter()
    snapshot_dt: datetime = utc_now()
    snapshot_ts = iso_utc(snapshot_dt)
    item_chunks = _chunks(config.item_ids, config.chunk_size)

    meta: dict[str, Any] = {
        "generated_at": snapshot_ts,
        "mode": "albion_raw_price_collection",
        "data_root": str(config.data_root),
        "albion_root": str(root),
        "servers": list(config.servers),
        "locations": list(config.locations),
        "qualities": list(config.qualities),
        "item_count": len(config.item_ids),
        "chunk_size": config.chunk_size,
        "chunks": len(item_chunks),
        "raw_files_written": 0,
        "normalized_rows_written": 0,
        "api_rows_seen": 0,
        "failures": [],
    }

    normalized_rows: list[dict[str, Any]] = []
    for server in config.servers:
        for chunk_index, item_chunk in enumerate(item_chunks, start=1):
            try:
                rows = fetch_prices(
                    server=server,
                    item_ids=item_chunk,
                    locations=config.locations,
                    qualities=config.qualities,
                    timeout_seconds=config.timeout_seconds,
                )
                meta["api_rows_seen"] += len(rows)
                raw_payload = {
                    "snapshot_ts": snapshot_ts,
                    "server": server,
                    "item_ids": list(item_chunk),
                    "locations": list(config.locations),
                    "qualities": list(config.qualities),
                    "source": "albion_online_data_project",
                    "rows": rows,
                }
                write_json_atomic(raw_snapshot_path(root, snapshot_dt, server, chunk_index), raw_payload)
                meta["raw_files_written"] += 1
                normalized_rows.extend(_normalize_price_row(row, server=server, snapshot_ts=snapshot_ts) for row in rows)
            except AlbionApiError as exc:
                meta["failures"].append({"server": server, "chunk": chunk_index, "error": str(exc)})
            except Exception as exc:
                meta["failures"].append({"server": server, "chunk": chunk_index, "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(config.polite_sleep_seconds)

    if normalized_rows:
        meta["normalized_rows_written"] = append_jsonl(normalized_jsonl_path(root, snapshot_dt), normalized_rows)

    meta["elapsed_ms"] = int(round((time.perf_counter() - started) * 1000))
    meta["failure_count"] = len(meta["failures"])
    write_json_atomic(latest_log_path(root), meta)
    print(
        "[albion_collector] "
        f"servers={','.join(config.servers)} rows={meta['normalized_rows_written']} "
        f"raw_files={meta['raw_files_written']} failures={meta['failure_count']} "
        f"elapsed_ms={meta['elapsed_ms']}",
        flush=True,
    )
    return meta
