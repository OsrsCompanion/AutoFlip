# PATCH: Replace get_market_cache_freshness

def get_market_cache_freshness(max_stale_minutes: int = 15) -> dict[str, Any]:
    cache = load_market_cache()
    updated_at = _parse_ts(cache.get("updated_at"))
    snapshot_bucket = _parse_ts(cache.get("snapshot_bucket"))
    reference_dt = snapshot_bucket or updated_at

    age_seconds = None
    is_stale = True
    status = "missing"
    freshness_label = "Unknown"
    age_display = "-"

    if reference_dt:
        age_seconds = max(0, int((_utc_now() - reference_dt).total_seconds()))
        age_minutes = round(age_seconds / 60, 2)
        is_stale = age_seconds > (max_stale_minutes * 60)
        status = "stale" if is_stale else "fresh"

        if age_minutes < 1:
            age_display = "<1m"
        elif age_minutes < 60:
            age_display = f"{int(age_minutes)}m"
        else:
            age_display = f"{round(age_minutes / 60, 1)}h"

        freshness_label = "Stale" if is_stale else "Fresh"

    return {
        "updated_at": cache.get("updated_at"),
        "snapshot_bucket": cache.get("snapshot_bucket"),
        "cache_item_count": len(cache.get("items", [])) if isinstance(cache, dict) else 0,
        "age_seconds": age_seconds,
        "age_minutes": None if age_seconds is None else round(age_seconds / 60, 2),
        "age_display": age_display,
        "freshness_label": freshness_label,
        "is_stale": is_stale,
        "status": status,
    }
