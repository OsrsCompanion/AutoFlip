from __future__ import annotations

import json
import sys

from app.services.item_metadata import ITEM_METADATA_PATH, write_item_metadata_cache


def main() -> int:
    try:
        payload = write_item_metadata_cache()
        print(json.dumps({
            "path": str(ITEM_METADATA_PATH),
            "item_count": payload.get("item_count", 0),
            "source": payload.get("source"),
            "schema_version": payload.get("schema_version"),
        }, indent=2))
        print()
        print("===================================")
        print("========= PATCH SUCCESS ===========")
        print("===================================")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print()
        print("===================================")
        print("=========== PATCH FAILED ==========")
        print("===================================")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
