
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
PLUGIN = ROOT / "app" / "routes" / "plugin.py"
MARKET = ROOT / "app" / "routes" / "market.py"


def backup(path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.bak_board_recommendation_routes_{STAMP}")
    shutil.copy2(path, backup_path)
    return backup_path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"missing patch target: {label}")
    return text.replace(old, new, 1)


def patch_plugin() -> dict[str, object]:
    text = PLUGIN.read_text(encoding="utf-8")
    original = text

    text = replace_once(
        text,
        "from app.services.board_optimizer import build_board_plan\nfrom app.services.recommendations import build_recommendations\n",
        "from app.services.board_optimizer import build_board_plan\n",
        "plugin import remove legacy recommender",
    )

    old_block = '''    settings = load_settings()
    if player_id:
        settings = build_player_settings_overlay(player_id, settings)
    result = build_recommendations(settings=settings, category_limit=limit, mode="plugin_full")
    result.update(
        {
            "plugin_ready": True,
            "user": payload.get("user"),
            "player_summary": summarize_player_state(player_id) if player_id else None,
        }
    )
    return result
'''

    new_block = '''    settings = load_settings()
    if player_id:
        settings = build_player_settings_overlay(player_id, settings)

    settings = dict(settings or {})
    settings["slots"] = limit
    board_plan = build_board_plan(settings=settings)
    board = list(board_plan.get("board") or [])[:limit]

    result = {
        **board_plan,
        "plugin_ready": True,
        "user": payload.get("user"),
        "player_summary": summarize_player_state(player_id) if player_id else None,
        "recommendations": board,
        "top_candidates": board,
        "legacy_recommendations_adapter": "board_optimizer_v1",
    }
    return result
'''
    text = replace_once(text, old_block, new_block, "plugin recommendations block")

    if text != original:
        backup(PLUGIN)
        PLUGIN.write_text(text, encoding="utf-8")

    return {"path": str(PLUGIN), "changed": text != original}


def patch_market() -> dict[str, object]:
    text = MARKET.read_text(encoding="utf-8")
    original = text

    text = replace_once(
        text,
        "from app.services.recommendations import build_recommendations\nfrom app.services.settings_store import load_settings\n",
        "from app.services.board_optimizer import build_board_plan\nfrom app.services.settings_store import load_settings\n",
        "market import board optimizer",
    )

    old_block = '''    settings = load_settings()
    if mode != "plugin_full":
        limit = min(limit, 5)
    cache = load_market_cache()
    meta = _cache_meta()

    refresh_started = False

    if cache.get("items"):
        snapshot = _recommendation_snapshot_from_cache(cache)
        result = build_recommendations(settings=settings, market_snapshot=snapshot, category_limit=limit, mode=mode)
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

    result = build_recommendations(
        settings=settings,
        market_snapshot={"items": [], "served_from": "cache_missing"},
        category_limit=limit,
        mode=mode,
    )
    if isinstance(result, dict):
        result.update(
            {
                "served_from": "cache_missing",
                "refresh_started": False,
                "cache_updated_at": cache.get("updated_at"),
                "snapshot_bucket": cache.get("snapshot_bucket"),
                "cache_read_only": True,
                **meta,
            }
        )
    return result
'''

    new_block = '''    settings = dict(load_settings() or {})
    if mode != "plugin_full":
        limit = min(limit, 5)

    cache = load_market_cache()
    meta = _cache_meta()
    settings["slots"] = limit

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
'''
    text = replace_once(text, old_block, new_block, "market recommendations block")

    if text != original:
        backup(MARKET)
        MARKET.write_text(text, encoding="utf-8")

    return {"path": str(MARKET), "changed": text != original}


def main() -> int:
    try:
        plugin_result = patch_plugin()
        market_result = patch_market()

        subprocess.run([sys.executable, "-m", "py_compile", str(PLUGIN), str(MARKET)], check=True)

        print("plugin:", plugin_result)
        print("market:", market_result)
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
