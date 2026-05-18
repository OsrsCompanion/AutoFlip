from __future__ import annotations

import argparse
import json
import sys

from app.services.market_intelligence_cache import backfill_market_intelligence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill derived market and recommendation candidate caches from historical market data."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=105,
        help="Historical days to scan once for windowed market intelligence. Default: 105.",
    )
    args = parser.parse_args(argv)
    print("[market-intelligence-backfill] starting", flush=True)
    print(f"[market-intelligence-backfill] days={args.days}", flush=True)
    result = backfill_market_intelligence(days=args.days)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    print("[market-intelligence-backfill] complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
