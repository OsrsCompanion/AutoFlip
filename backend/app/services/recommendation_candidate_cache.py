from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services import market_intelligence_cache as mic

__all__ = [
    "build_recommendation_candidate_cache",
    "load_recommendation_candidate_cache",
    "normalize_candidate_quantity_fields",
]


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def normalize_candidate_quantity_fields(payload: Any) -> Any:
    """
    Compatibility layer for the quantity schema migration.

    Canonical meaning:
      liquidity_safe_quantity = market/liquidity-safe execution cap
      suggested_quantity       = legacy compatibility alias
      max_quantity             = legacy compatibility alias

    This intentionally does NOT treat any of these as the GE buy limit.
    Buy limits come from item_metadata.json.
    """
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
            # Preserve legacy callers during migration.
            row.setdefault("suggested_quantity", liquidity_safe_quantity)
            row.setdefault("max_quantity", liquidity_safe_quantity)

        metadata = row.get("scoring_metadata")
        if isinstance(metadata, dict):
            metadata.setdefault("quantity_schema", "liquidity_safe_quantity_v1")

    return payload


def _write_candidate_cache(payload: dict[str, Any]) -> None:
    path = Path(mic.RECOMMENDATION_CANDIDATE_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_recommendation_candidate_cache(derived_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = mic.build_recommendation_candidate_cache(derived_cache)
    payload = normalize_candidate_quantity_fields(payload)
    if isinstance(payload, dict):
        _write_candidate_cache(payload)
    return payload if isinstance(payload, dict) else {"items": [], "candidates": []}


def load_recommendation_candidate_cache() -> dict[str, Any]:
    payload = mic.load_recommendation_candidate_cache()
    payload = normalize_candidate_quantity_fields(payload)
    return payload if isinstance(payload, dict) else {"items": [], "candidates": []}
