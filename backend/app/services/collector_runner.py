from __future__ import annotations

import argparse
import gc
import os
import time
import traceback
from datetime import UTC, datetime

from app.services.wiki_prices import get_market_snapshot

INTERVAL_SECONDS = int(os.getenv("OSRS_COLLECTOR_INTERVAL_SECONDS", "60"))


def _diagnostics_enabled(args: argparse.Namespace) -> bool:
    return bool(args.diagnostic_cycles or os.getenv("OSRS_COLLECTOR_DIAGNOSTICS") == "1")


def _checkpoint(enabled: bool, label: str, extra: dict | None = None) -> None:
    if not enabled:
        return
    try:
        from app.services.collector_diagnostics import checkpoint

        payload = checkpoint(label, extra=extra)
        rss_mb = (payload.get("process") or {}).get("rss_mb")
        print(f"[collector][diag] {label} rss_mb={rss_mb}", flush=True)
    except Exception as exc:
        print(f"[collector][diag] checkpoint_failed label={label} error={exc}", flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the OSRS market collector")
    parser.add_argument(
        "--diagnostic-cycles",
        type=int,
        default=0,
        help="Run this many collector cycles with memory/storage checkpoints, then exit.",
    )
    args = parser.parse_args(argv)

    diag_enabled = _diagnostics_enabled(args)
    cycle = 0

    print("[collector] starting OSRS collector")
    print(f"[collector] interval={INTERVAL_SECONDS}s")
    print(f"[collector] data_root={os.getenv('OSRS_FLIP_DATA_ROOT')}")
    print("[collector] graph_cache_mode=incremental_only")
    if diag_enabled:
        print("[collector] diagnostics=enabled")

    while True:
        cycle += 1
        started = datetime.now(UTC).isoformat()
        _checkpoint(diag_enabled, "cycle_start", {"cycle": cycle, "started": started})

        try:
            _checkpoint(diag_enabled, "before_get_market_snapshot", {"cycle": cycle})
            snapshot = get_market_snapshot()
            _checkpoint(
                diag_enabled,
                "after_get_market_snapshot",
                {
                    "cycle": cycle,
                    "bucket": snapshot.get("snapshot_bucket"),
                    "items": snapshot.get("item_count"),
                },
            )
            print(
                f"[collector] ok time={started} "
                f"bucket={snapshot.get('snapshot_bucket')} "
                f"items={snapshot.get('item_count')}"
            )
        except KeyboardInterrupt:
            print("[collector] stopped by keyboard interrupt")
            raise
        except Exception:
            print(f"[collector] ERROR time={started}")
            traceback.print_exc()
        finally:
            if diag_enabled:
                collected = gc.collect()
                _checkpoint(diag_enabled, "cycle_end_after_gc", {"cycle": cycle, "gc_collected": collected})

        if args.diagnostic_cycles and cycle >= args.diagnostic_cycles:
            print(f"[collector] diagnostic cycles complete cycles={cycle}")
            return

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
