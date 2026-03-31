# PATCH: Modify _cache_meta

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
        "freshness_text": f"{cache_meta.get('freshness_label')} ({cache_meta.get('age_display')})",
    }
