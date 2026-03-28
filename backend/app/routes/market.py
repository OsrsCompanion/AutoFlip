from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query

from app.services.ai_context import build_ai_context_for_query, build_ai_item_context
from app.services.market_history import (
    ensure_history_and_cache,
    get_item_history_payload,
    get_market_cache_freshness,
    load_market_cache,
    load_tracked_universe,
    search_cache,
)
from app.services.recommendations import build_recommendations
from app.services.settings_store import load_settings
from app.services.trade_decisions import build_trade_decisions
from app.services.wiki_prices import get_market_snapshot

router = APIRouter(prefix="/market", tags=["market"])

_REFRESH_LOCK = Lock()
_REFRESH_STATE: dict[str, object] = {
    "running": False,
    "last_started_at": None,
    "last_completed_at": None,
    "last_error": None,
}


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _run_market_refresh_task() -> None:
    with _REFRESH_LOCK:
        if bool(_REFRESH_STATE.get("running")):
            return
        _REFRESH_STATE["running"] = True
        _REFRESH_STATE["last_started_at"] = _iso_now()
        _REFRESH_STATE["last_error"] = None

    try:
        snapshot = get_market_snapshot()
        items = []
        if isinstance(snapshot, dict):
            items = snapshot.get("items") or []
        if items:
            ensure_history_and_cache(items)
        _REFRESH_STATE["last_completed_at"] = _iso_now()
    except Exception as exc:  # pragma: no cover - defensive background path
        _REFRESH_STATE["last_error"] = str(exc)
    finally:
        _REFRESH_STATE["running"] = False


def _schedule_market_refresh(background_tasks: BackgroundTasks | None) -> bool:
    if bool(_REFRESH_STATE.get("running")):
        return False
    if background_tasks is None:
        return False
    background_tasks.add_task(_run_market_refresh_task)
    return True


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
):
    settings = load_settings()
    cache = load_market_cache()
    meta = _cache_meta()

    refresh_started = False
    if meta.get("is_stale") or not cache.get("items"):
        refresh_started = _schedule_market_refresh(background_tasks)

    if cache.get("items"):
        snapshot = _recommendation_snapshot_from_cache(cache)
        result = build_recommendations(settings=settings, market_snapshot=snapshot, category_limit=limit)
        if isinstance(result, dict):
            result.update(
                {
                    "served_from": "cache",
                    "refresh_started": refresh_started,
                    "cache_updated_at": cache.get("updated_at"),
                    "snapshot_bucket": cache.get("snapshot_bucket"),
                    **meta,
                }
            )
        return result

    snapshot = get_market_snapshot()
    items = snapshot.get("items") if isinstance(snapshot, dict) else []
    if items:
        ensure_history_and_cache(items)

    result = build_recommendations(settings=settings, market_snapshot=snapshot, category_limit=limit)
    if isinstance(result, dict):
        refreshed_cache = load_market_cache()
        result.update(
            {
                "served_from": "live_fallback",
                "refresh_started": refresh_started,
                "cache_updated_at": refreshed_cache.get("updated_at"),
                "snapshot_bucket": refreshed_cache.get("snapshot_bucket") or result.get("snapshot_bucket"),
                **_cache_meta(),
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


@router.get("/explorer/history/{item_id}")
def market_explorer_history(
    item_id: int,
    background_tasks: BackgroundTasks,
    range_name: str = Query(default="month", alias="range"),
):
    refresh_started = False
    if range_name == "day" and _should_refresh_intraday() and not bool(_REFRESH_STATE.get("running")):
        refresh_started = _schedule_market_refresh(background_tasks)

    payload = get_item_history_payload(item_id=item_id, window=range_name)
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
