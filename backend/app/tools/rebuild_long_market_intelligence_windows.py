from __future__ import annotations

import argparse
import json

from app.services.market_intelligence_cache import LONG_WINDOW_NAMES, rebuild_long_market_intelligence_windows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild corrupted long-window market intelligence metrics from raw history once."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=105,
        help="Historical days to scan for the selected long windows. Default: 105.",
    )
    parser.add_argument(
        "--windows",
        nargs="*",
        default=list(LONG_WINDOW_NAMES),
        help="Window names to rebuild. Default: 10d 30d 90d 105d.",
    )
    args = parser.parse_args()

    print("[market-intelligence-long-window-rebuild] starting", flush=True)
    print(f"[market-intelligence-long-window-rebuild] windows={args.windows}", flush=True)
    print(f"[market-intelligence-long-window-rebuild] days={args.days}", flush=True)
    result = rebuild_long_market_intelligence_windows(window_names=args.windows, days=args.days)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    print("[market-intelligence-long-window-rebuild] complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
