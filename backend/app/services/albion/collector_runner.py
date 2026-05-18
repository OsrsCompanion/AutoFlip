from __future__ import annotations

import argparse
import signal
import time

from .collector import collect_once
from .config import default_config

_STOP = False


def _handle_stop(signum, frame) -> None:  # type: ignore[no-untyped-def]
    global _STOP
    _STOP = True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run isolated Albion Online raw market collector")
    parser.add_argument("--interval", type=int, default=1800, help="Seconds between collection passes. Default: 1800")
    parser.add_argument("--once", action="store_true", help="Run one collection pass and exit")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    config = default_config()
    while not _STOP:
        try:
            collect_once(config)
        except Exception as exc:
            print(f"[albion_collector] pass_failed error={type(exc).__name__}: {exc}", flush=True)
        if args.once:
            break
        slept = 0
        sleep_for = max(int(args.interval), 60)
        while slept < sleep_for and not _STOP:
            step = min(5, sleep_for - slept)
            time.sleep(step)
            slept += step


if __name__ == "__main__":
    main()
