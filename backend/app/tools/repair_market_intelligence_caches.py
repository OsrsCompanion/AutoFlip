from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import market_history as mh
from app.services.market_intelligence_cache import (
    DERIVED_MARKET_CACHE_PATH,
    MARKET_INTELLIGENCE_STATE_PATH,
    MARKET_INTELLIGENCE_WINDOWS_PATH,
    RECOMMENDATION_CANDIDATE_CACHE_PATH,
    build_market_intelligence_caches,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: str | Path, payload: Any) -> None:
    mh._ensure_dirs()
    mh._write_json(str(path), payload)


def _size(path: str | Path) -> int:
    p = Path(path)
    return p.stat().st_size if p.exists() else 0


def repair_market_intelligence_caches() -> dict[str, Any]:
    """Repair the disposable derived intelligence cache layer.

    This does not modify raw market history. It safely migrates the accidental
    oversized state file into market_intelligence_windows.json, compacts state
    metadata, and rebuilds derived/candidate caches from current market cache.
    """
    state = _read_json(MARKET_INTELLIGENCE_STATE_PATH, {})
    windows = _read_json(MARKET_INTELLIGENCE_WINDOWS_PATH, {})
    migrated_from_state = False

    if isinstance(state, dict) and isinstance(state.get("items"), dict):
        items = state.get("items", {})
        windows = {
            "cache_type": "market_intelligence_windows",
            "schema_version": state.get("schema_version", 2),
            "mode": state.get("mode", "migrated_from_oversized_state"),
            "updated_at": state.get("updated_at") or _utc_now_iso(),
            "migrated_at": _utc_now_iso(),
            "source_days": state.get("source_days"),
            "window_names": state.get("window_names", []),
            "scanned_history_rows": state.get("scanned_history_rows"),
            "used_window_samples": state.get("used_window_samples"),
            "item_count": state.get("item_count") or len(items),
            "items": items,
        }
        _write_json(MARKET_INTELLIGENCE_WINDOWS_PATH, windows)
        compact_state = {k: v for k, v in state.items() if k != "items"}
        compact_state.update({
            "items_removed_from_state": True,
            "windows_path": MARKET_INTELLIGENCE_WINDOWS_PATH,
            "repaired_at": _utc_now_iso(),
            "repair_note": "Moved oversized per-item window analytics out of state file.",
        })
        _write_json(MARKET_INTELLIGENCE_STATE_PATH, compact_state)
        migrated_from_state = True

    market_cache = mh.load_market_cache()
    derived, candidates = build_market_intelligence_caches(market_cache)

    candidate_items = candidates.get("items", []) if isinstance(candidates.get("items"), list) else []
    candidate_list = candidates.get("candidates", []) if isinstance(candidates.get("candidates"), list) else []

    return {
        "status": "ok",
        "raw_history_modified": False,
        "migrated_from_oversized_state": migrated_from_state,
        "state_path": MARKET_INTELLIGENCE_STATE_PATH,
        "state_size_bytes": _size(MARKET_INTELLIGENCE_STATE_PATH),
        "windows_path": MARKET_INTELLIGENCE_WINDOWS_PATH,
        "windows_size_bytes": _size(MARKET_INTELLIGENCE_WINDOWS_PATH),
        "derived_path": DERIVED_MARKET_CACHE_PATH,
        "derived_size_bytes": _size(DERIVED_MARKET_CACHE_PATH),
        "derived_item_count": derived.get("item_count", 0),
        "candidate_path": RECOMMENDATION_CANDIDATE_CACHE_PATH,
        "candidate_size_bytes": _size(RECOMMENDATION_CANDIDATE_CACHE_PATH),
        "candidate_count": candidates.get("candidate_count", 0),
        "candidate_items_count": len(candidate_items),
        "candidate_candidates_count": len(candidate_list),
    }


def main() -> int:
    print("[market-intelligence-repair] starting", flush=True)
    result = repair_market_intelligence_caches()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    print("[market-intelligence-repair] complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
