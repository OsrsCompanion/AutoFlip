from __future__ import annotations

import json
import sys
from pathlib import Path

CACHE_PATH = Path("/mnt/nvme/autoflip-data/cache/recommendation_candidate_cache.json")


def _to_int(value, default=0):
    try:
        return int(value or 0)
    except Exception:
        return default


def normalize_candidate_quantity_fields(payload):
    if isinstance(payload, dict):
        rows = payload.get("candidates") or payload.get("items") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    if not isinstance(rows, list):
        return payload

    for row in rows:
        if not isinstance(row, dict):
            continue

        liquidity_safe_quantity = _to_int(
            row.get("liquidity_safe_quantity")
            or row.get("suggested_quantity")
            or row.get("max_quantity")
        )

        if liquidity_safe_quantity > 0:
            row["liquidity_safe_quantity"] = liquidity_safe_quantity
            row.setdefault("suggested_quantity", liquidity_safe_quantity)
            row.setdefault("max_quantity", liquidity_safe_quantity)

        meta = row.get("scoring_metadata")
        if isinstance(meta, dict):
            meta.setdefault("quantity_schema", "liquidity_safe_quantity_v1")

    return payload


def main() -> int:
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        payload = normalize_candidate_quantity_fields(payload)

        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        tmp.replace(CACHE_PATH)

        rows = payload.get("candidates") or payload.get("items") or []
        count = sum(1 for row in rows if isinstance(row, dict) and _to_int(row.get("liquidity_safe_quantity")) > 0)

        print(json.dumps({
            "path": str(CACHE_PATH),
            "candidate_count": len(rows),
            "with_liquidity_safe_quantity": count,
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
