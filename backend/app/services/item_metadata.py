from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

ITEM_METADATA_PATH = Path("/mnt/nvme/autoflip-data/cache/item_metadata.json")
OSRS_MAPPING_URL = "https://prices.runescape.wiki/api/v1/osrs/mapping"
USER_AGENT = "AutoFlip item metadata sync - local deployment"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def normalize_mapping_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        item_id = _safe_int(row.get("id"))
        if item_id <= 0:
            continue

        limit = row.get("limit")
        buy_limit = _safe_int(limit) if limit is not None else 0

        by_id[str(item_id)] = {
            "id": item_id,
            "item_id": item_id,
            "name": str(row.get("name") or "").strip(),
            "buy_limit": buy_limit,
            "limit": buy_limit,
            "members": bool(row.get("members")),
            "highalch": _safe_int(row.get("highalch")),
            "lowalch": _safe_int(row.get("lowalch")),
            "value": _safe_int(row.get("value")),
            "icon": str(row.get("icon") or "").strip(),
        }

    return {
        "cache_type": "item_metadata",
        "schema_version": 1,
        "source": "osrs_wiki_mapping",
        "updated_at": int(time.time()),
        "item_count": len(by_id),
        "items": by_id,
    }


def fetch_osrs_mapping(timeout: float = 20.0) -> list[dict[str, Any]]:
    req = urllib.request.Request(OSRS_MAPPING_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("OSRS mapping endpoint returned unexpected shape")
    return [row for row in data if isinstance(row, dict)]


def write_item_metadata_cache(path: Path = ITEM_METADATA_PATH, *, timeout: float = 20.0) -> dict[str, Any]:
    rows = fetch_osrs_mapping(timeout=timeout)
    payload = normalize_mapping_rows(rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

    return payload


def load_item_metadata_cache(path: Path = ITEM_METADATA_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}

    return {str(k): v for k, v in items.items() if isinstance(v, dict)}


def get_item_metadata(item_id: int, metadata: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if metadata is None:
        metadata = load_item_metadata_cache()
    return metadata.get(str(item_id), {}) if item_id > 0 else {}


def get_buy_limit(item_id: int, metadata: dict[str, dict[str, Any]] | None = None) -> int:
    row = get_item_metadata(item_id, metadata)
    return _safe_int(row.get("buy_limit") or row.get("limit"))
