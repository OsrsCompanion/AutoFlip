from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
import json
import os
import re

from fastapi import APIRouter, BackgroundTasks, Query

from app.services.ai_context import build_ai_context_for_query, build_ai_item_context
from app.services.market_history import (
    get_market_cache_freshness,
    get_storage_debug_meta,
    load_market_cache,
    load_tracked_universe,
    search_cache,
)
from app.services.board_optimizer import build_board_plan
from app.services.settings_store import load_settings
from app.services.trade_decisions import build_trade_decisions

router = APIRouter(prefix="/market", tags=["market"])

_REFRESH_LOCK = Lock()
_REFRESH_STATE: dict[str, object] = {
    "running": False,
    "last_started_at": None,
    "last_completed_at": None,
    "last_error": None,
}

HOT_ITEMS_LOG_PATH = (
    Path(os.getenv("OSRS_FLIP_DATA_ROOT", "/mnt/nvme/autoflip-data"))
    / "cache"
    / "hot_items.jsonl"
)
URGENT_REFRESH_COOLDOWN_SECONDS = int(os.getenv("OSRS_URGENT_REFRESH_COOLDOWN_SECONDS", "300"))
_URGENT_REFRESH_LOCK = Lock()
_URGENT_REFRESH_STATE: dict[str, float] = {}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _slugify_item_name(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _market_item_public_payload(item: dict[str, Any]) -> dict[str, Any]:
    item_id = item.get("id") or item.get("item_id")
    name = str(item.get("name") or item.get("item_name") or f"Item {item_id}")
    return {
        **item,
        "id": item_id,
        "item_id": item_id,
        "name": name,
        "item_name": name,
        "slug": str(item.get("slug") or _slugify_item_name(name)),
    }


def resolve_market_item(slug_or_id: str) -> dict[str, Any] | None:
    raw = str(slug_or_id or "").strip()
    if not raw:
        return None

    cache = load_market_cache()
    items = cache.get("items", []) if isinstance(cache, dict) else []
    if not isinstance(items, list):
        return None

    if raw.isdigit():
        wanted_id = int(raw)
        for item in items:
            if isinstance(item, dict) and int(item.get("id") or item.get("item_id") or 0) == wanted_id:
                return _market_item_public_payload(item)

    wanted_slug = _slugify_item_name(raw)
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("item_name") or "")
        item_slug = str(item.get("slug") or _slugify_item_name(name))
        if item_slug == wanted_slug or _slugify_item_name(name) == wanted_slug:
            return _market_item_public_payload(item)

    return None


def _record_hot_item_access(item_id: int, range_name: str) -> None:
    """Record lightweight graph interest.

    Web/API remains cache read-only. This writes only tiny interest telemetry so
    the collector can prioritize graph cache updates inside its existing budget.
    """
    try:
        HOT_ITEMS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "item_id": int(item_id),
            "range": str(range_name),
            "ts": _iso_now(),
        }
        with HOT_ITEMS_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except Exception as exc:
        print(f"[market_route] hot_item_record_failed item={item_id} range={range_name} error={exc}", flush=True)


def _urgent_refresh_allowed(item_id: int, range_name: str) -> tuple[bool, float]:
    key = f"{int(item_id)}:{str(range_name)}"
    now_ts = datetime.now(UTC).timestamp()

    with _URGENT_REFRESH_LOCK:
        last_ts = float(_URGENT_REFRESH_STATE.get(key) or 0)
        remaining = max(0.0, URGENT_REFRESH_COOLDOWN_SECONDS - (now_ts - last_ts))

        if remaining > 0:
            return False, remaining

        _URGENT_REFRESH_STATE[key] = now_ts
        return True, 0.0


def _read_graph_payload(item_id: int, range_name: str) -> dict[str, Any]:
    try:
        from app.services.market_history import _read_history_graph_cache

        cached_payload = _read_history_graph_cache(item_id=item_id, window=range_name)
        if isinstance(cached_payload, dict):
            return cached_payload
    except Exception as exc:
        print(f"[market_route] graph_cache_read_failed item={item_id} range={range_name} error={exc}", flush=True)

    return {
        "points": [],
        "graph_cache_missing": True,
        "served_from_graph_cache": False,
        "storage_mode": "graph_cache_only",
        "point_source": "graph_cache_required",
        "cache_read_only": True,
    }


def _run_market_refresh_task() -> None:
    """Web/API is cache read-only."""
    with _REFRESH_LOCK:
        _REFRESH_STATE["running"] = False
        _REFRESH_STATE["last_started_at"] = _iso_now()
        _REFRESH_STATE["last_completed_at"] = _iso_now()
        _REFRESH_STATE["last_error"] = "web_refresh_disabled_cache_read_only"


def _schedule_market_refresh(background_tasks: BackgroundTasks | None) -> bool:
    _REFRESH_STATE["last_error"] = "web_refresh_disabled_cache_read_only"
    return False


def _cache_meta() -> dict[str, object]:
    cache_meta = get_market_cache_freshness()
    universe = load_tracked_universe()
    return {
        **cache_meta,
        "tracked_count": len(universe.get("tracked_ids", [])),
        "refresh_running": bool(_REFRESH_STATE.get("running")),
        "refresh_last_started_at": _REFRESH_STATE.get("last_started_at"),
        "refresh_last_completed_at": _REFRESH_STATE.get("last_completed_at"),
        "refresh_last_error": _REFRESH_STATE.get("last_error"),
    }


def _should_refresh_intraday(max_age_seconds: int = 45) -> bool:
    completed_at = _REFRESH_STATE.get("last_completed_at")
    completed_dt = datetime.fromisoformat(str(completed_at)) if completed_at else None
    if not completed_dt:
        return True
    return completed_dt < (datetime.now(UTC) - timedelta(seconds=max_age_seconds))


def _recommendation_snapshot_from_cache(cache: dict[str, Any]) -> dict[str, Any]:
    cache_items = cache.get("items", []) if isinstance(cache, dict) else []
    snapshot_items: list[dict[str, Any]] = []

    for item in cache_items:
        snapshot_items.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "low": item.get("buy_price", 0),
                "high": item.get("sell_price", 0),
                "recent_volume": item.get("recent_volume", 0),
                "limit": item.get("buy_limit", 0),
                "members": item.get("members", False),
                "high_alch": item.get("high_alch", 0),
                "high_alch_value": item.get("high_alch", 0),
                "buy_price": item.get("buy_price", 0),
                "sell_price": item.get("sell_price", 0),
                "profit_per_item": item.get("profit_per_item", 0),
                "roi_pct": item.get("roi_pct", 0),
                "day_low": item.get("day_low", 0),
                "day_high": item.get("day_high", 0),
                "week_low": item.get("week_low", 0),
                "week_high": item.get("week_high", 0),
                "month_low": item.get("month_low", 0),
                "month_high": item.get("month_high", 0),
                "avg_daily_volume": item.get("avg_daily_volume", 0),
                "updated_at": item.get("updated_at") or cache.get("snapshot_bucket"),
            }
        )

    return {
        "items": snapshot_items,
        "updated_at": cache.get("updated_at"),
        "snapshot_bucket": cache.get("snapshot_bucket"),
        "served_from": "cache",
    }


@router.get("/decisions")
def market_decisions():
    settings = load_settings()
    current_scan = settings.get("last_scan") or {"offers": []}
    return build_trade_decisions(settings=settings, current_scan=current_scan)


@router.get("/recommendations")
def market_recommendations(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=10, ge=1, le=50),
    mode: str = Query(default="web_safe"),
    budget: str | None = Query(default=None),
    hours_away: float | None = Query(default=None, ge=0, le=48),
    risk_percent: int | None = Query(default=None, ge=0, le=100),
):
    settings = dict(load_settings() or {})

    if mode != "plugin_full":
        limit = min(limit, 5)

    cache = load_market_cache()
    meta = _cache_meta()

    settings["slots"] = limit
    if budget is not None:
        settings["budget"] = budget
    if hours_away is not None:
        settings["hours_away"] = hours_away
    if risk_percent is not None:
        risk_value = int(risk_percent)
        if risk_value <= 30:
            settings["risk_mode"] = "safe"
        elif risk_value >= 70:
            settings["risk_mode"] = "aggressive"
        else:
            settings["risk_mode"] = "balanced"
        settings["risk_percent"] = risk_value

    result = build_board_plan(settings=settings)

    board = list(result.get("board") or [])[:limit]

    result.update(
        {
            "recommendations": board,
            "top_candidates": board,
            "served_from": "recommendation_candidate_cache",
            "refresh_started": False,
            "cache_updated_at": cache.get("updated_at"),
            "snapshot_bucket": cache.get("snapshot_bucket"),
            "cache_read_only": True,
            "legacy_recommendations_adapter": "board_optimizer_v1",
            **meta,
        }
    )

    return result


@router.get("/ai-context/item/{item_id}")
def market_ai_context_item(item_id: int):
    settings = load_settings()
    current_scan = settings.get("last_scan") or {"offers": []}
    payload = build_ai_item_context(item_id=item_id, current_scan=current_scan)
    return {
        **_cache_meta(),
        **payload,
    }


@router.get("/ai-context/search")
def market_ai_context_search(q: str = Query(default="", min_length=1)):
    settings = load_settings()
    current_scan = settings.get("last_scan") or {"offers": []}
    payload = build_ai_context_for_query(query=q, current_scan=current_scan)
    return {
        **_cache_meta(),
        **payload,
    }


@router.get("/explorer/item/{slug_or_id}")
def market_explorer_item(slug_or_id: str):
    item = resolve_market_item(slug_or_id)
    found = item is not None
    return {
        "query": slug_or_id,
        **_cache_meta(),
        "served_from": "cache",
        "found": found,
        "item": item,
        "items": [item] if found else [],
    }


@router.get("/explorer/bootstrap")
def market_explorer_bootstrap(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=100, ge=1, le=250),
    refresh: bool = Query(default=True),
):
    cache = load_market_cache()
    meta = _cache_meta()
    refresh_started = False
    if refresh and (meta.get("is_stale") or not cache.get("items")):
        refresh_started = _schedule_market_refresh(background_tasks)

    items = search_cache(query="", limit=limit)
    return {
        "query": "",
        **meta,
        "served_from": "cache",
        "refresh_started": refresh_started,
        "items": items,
    }


@router.post("/refresh-kick")
def market_refresh_kick(background_tasks: BackgroundTasks):
    refresh_started = _schedule_market_refresh(background_tasks)
    return {
        "status": "scheduled" if refresh_started else "already_running",
        "refresh_started": refresh_started,
        **_cache_meta(),
    }


@router.get("/explorer/search")
def market_explorer_search(q: str = "", limit: int = Query(default=100, ge=1, le=250)):
    meta = _cache_meta()
    return {
        "query": q,
        **meta,
        "served_from": "cache",
        "items": search_cache(query=q, limit=limit),
    }



def _is_corrupt_day_graph_cache(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False

    points = payload.get("points")
    point_count = len(points) if isinstance(points, list) else int(payload.get("point_count") or 0)
    raw_point_count = int(payload.get("raw_point_count") or 0)

    if payload.get("storage_mode") == "snapshot_only" and point_count <= 3:
        return True

    if payload.get("window_strategy") == "incremental_append_trim" and point_count <= 3:
        return True

    if raw_point_count >= 100 and point_count <= 12:
        return True

    return False


def _repair_corrupt_day_graph_cache(item_id: int) -> dict[str, Any]:
    from app.services.market_history import (
        get_item_history_payload,
        _write_history_graph_cache,
    )

    rebuilt = get_item_history_payload(
        item_id=item_id,
        window="day",
        use_graph_cache=False,
    )

    rebuilt["corrupt_graph_cache_repaired"] = True

    _write_history_graph_cache(
        item_id=item_id,
        window="day",
        payload=rebuilt,
    )

    return rebuilt


@router.get("/explorer/history/{item_id}")
def market_explorer_history(
    item_id: int,
    background_tasks: BackgroundTasks,
    range_name: str = Query(default="week", alias="range"),
):
    refresh_started = False
    _record_hot_item_access(item_id, range_name)

    payload = None

    # Fast path: serve existing graph-cache JSON directly.
    # This prevents the UI from timing out on day graphs while the slow raw-history
    # rebuild path scans millions of rows.
    try:
        from app.services.market_history import _read_history_graph_cache
        cached_payload = _read_history_graph_cache(item_id=item_id, window=range_name)
        if isinstance(cached_payload, dict) and isinstance(cached_payload.get("points"), list) and cached_payload.get("points"):
            if range_name == "day" and _is_corrupt_day_graph_cache(cached_payload):
                payload = _repair_corrupt_day_graph_cache(item_id)
            else:
                payload = cached_payload
    except Exception as exc:
        print(f"[market_route] graph_cache_fast_path_failed item={item_id} range={range_name} error={exc}", flush=True)

    if payload is None:
        if range_name == "day":
            try:
                payload = _repair_corrupt_day_graph_cache(item_id)
                payload["missing_graph_cache_rebuilt"] = True
            except Exception as exc:
                print(f"[market_route] missing_day_graph_rebuild_failed item={item_id} error={exc}", flush=True)
                payload = {
                    "points": [],
                    "graph_cache_missing": True,
                    "served_from_graph_cache": False,
                    "storage_mode": "graph_cache_only",
                    "point_source": "graph_cache_required",
                    "cache_read_only": True,
                }
        else:
            payload = {
                "points": [],
                "graph_cache_missing": True,
                "served_from_graph_cache": False,
                "storage_mode": "graph_cache_only",
                "point_source": "graph_cache_required",
                "cache_read_only": True,
            }
    history = payload.get("points", []) if isinstance(payload, dict) else []
    return {
        "item_id": item_id,
        "range": range_name,
        "refresh_started": refresh_started,
        **_cache_meta(),
        **(payload if isinstance(payload, dict) else {}),
        "points": history,
        "timestamps": [point.get("snapshot_ts") or point.get("ts") or point.get("timestamp") or point.get("bucket") for point in history],
    }


@router.post("/explorer/history/{item_id}/refresh")
def market_explorer_history_refresh(
    item_id: int,
    range_name: str = Query(default="day", alias="range"),
):
    """Urgently refresh exactly one item/window graph.

    This is intentionally bounded and does not fetch live market data or rebuild
    history. It only refreshes one graph cache file from current market_cache.json.
    """
    _record_hot_item_access(item_id, range_name)

    allowed, cooldown_remaining = _urgent_refresh_allowed(item_id, range_name)
    refresh_meta: dict[str, Any] = {
        "urgent_single_item_refresh": False,
        "urgent_refresh_status": "cooldown",
        "urgent_refresh_cooldown_remaining_seconds": round(cooldown_remaining, 3),
    }

    if allowed:
        try:
            from app.services.graph_cache_updater import refresh_single_item_graph_from_market_cache

            refresh_meta = refresh_single_item_graph_from_market_cache(item_id=item_id, window=range_name)
            refresh_meta["urgent_refresh_cooldown_remaining_seconds"] = 0
        except Exception as exc:
            refresh_meta = {
                "urgent_single_item_refresh": False,
                "urgent_refresh_status": "error",
                "urgent_refresh_error": str(exc),
                "urgent_refresh_cooldown_remaining_seconds": 0,
            }

    payload = _read_graph_payload(item_id, range_name)
    history = payload.get("points", []) if isinstance(payload, dict) else []

    return {
        "item_id": item_id,
        "range": range_name,
        **_cache_meta(),
        **refresh_meta,
        **(payload if isinstance(payload, dict) else {}),
        "points": history,
        "timestamps": [point.get("snapshot_ts") or point.get("ts") or point.get("timestamp") or point.get("bucket") for point in history],
    }


@router.get("/history/{item_id}")
def market_history_compat(
    item_id: int,
    background_tasks: BackgroundTasks,
    range_name: str = Query(default="week", alias="range"),
):
    payload = market_explorer_history(item_id=item_id, background_tasks=background_tasks, range_name=range_name)
    payload["history_debug"] = {
        **get_storage_debug_meta(),
        "alias": "/market/history/{item_id}",
    }
    return payload
