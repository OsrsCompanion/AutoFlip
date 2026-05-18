from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/mnt/nvme/autoflip-data/albion")
LATEST = ROOT / "logs" / "albion_collector_latest.json"


def main() -> None:
    if not LATEST.exists():
        raise SystemExit(f"missing latest collector log: {LATEST}")
    meta = json.load(LATEST.open("r", encoding="utf-8"))
    rows = int(meta.get("normalized_rows_written") or 0)
    raw_files = int(meta.get("raw_files_written") or 0)
    failures = int(meta.get("failure_count") or 0)
    print(json.dumps(meta, indent=2, sort_keys=True))
    if rows <= 0:
        raise SystemExit("verification failed: no normalized Albion rows were written")
    if raw_files <= 0:
        raise SystemExit("verification failed: no raw Albion API payload files were written")
    if failures:
        print(f"warning: collector completed with {failures} failed chunks")
    print("albion collector verification passed")


if __name__ == "__main__":
    main()
