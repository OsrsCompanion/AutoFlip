
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "app" / "services" / "board_optimizer.py"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.bak_membership_filter_{STAMP}")
    shutil.copy2(path, backup_path)
    return backup_path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f"missing patch target: {label}")
    return text.replace(old, new, 1)


def main() -> int:
    try:
        text = TARGET.read_text(encoding="utf-8")
        original = text

        text = replace_once(
            text,
            'DEFAULT_RISK_MODE = "balanced"\n\nRISK_MODES = {',
            'DEFAULT_RISK_MODE = "balanced"\nDEFAULT_MEMBERSHIP_MODE = "members"\n\nRISK_MODES = {',
            "default_membership_mode",
        )

        text = replace_once(
            text,
            '''def _normalize_risk_mode(value: Any) -> str:
    text = str(value or DEFAULT_RISK_MODE).strip().lower().replace("_", "-")
    if text in {"safe", "safe-turnover", "low", "conservative"}:
        return "safe"
    if text in {"aggressive", "high"}:
        return "aggressive"
    return "balanced"


def _horizon_factor''',
            '''def _normalize_risk_mode(value: Any) -> str:
    text = str(value or DEFAULT_RISK_MODE).strip().lower().replace("_", "-")
    if text in {"safe", "safe-turnover", "low", "conservative"}:
        return "safe"
    if text in {"aggressive", "high"}:
        return "aggressive"
    return "balanced"


def _normalize_membership_mode(value: Any) -> str:
    text = str(value or DEFAULT_MEMBERSHIP_MODE).strip().lower().replace("_", "-")
    if text in {"f2p", "free", "free-to-play", "freeplay", "non-members", "nonmembers"}:
        return "f2p"
    return "members"


def _item_is_members_only(item_id: int, metadata: dict[str, dict[str, Any]]) -> bool:
    row = metadata.get(str(item_id), {}) if item_id > 0 else {}
    return bool(row.get("members"))


def _membership_allowed(candidate: dict[str, Any], *, membership_mode: str, metadata: dict[str, dict[str, Any]]) -> bool:
    if membership_mode != "f2p":
        return True
    item_id = _to_int(candidate.get("item_id") or candidate.get("id"))
    return not _item_is_members_only(item_id, metadata)


def _horizon_factor''',
            "membership_helpers",
        )

        text = replace_once(
            text,
            '    risk_mode = _normalize_risk_mode(settings.get("risk_mode") or settings.get("risk") or settings.get("preset"))\n    ml_weight = max(0.0, min(1.0, _to_float(settings.get("ml_weight"), 0.0)))\n    metadata = load_item_metadata_cache()\n',
            '    risk_mode = _normalize_risk_mode(settings.get("risk_mode") or settings.get("risk") or settings.get("preset"))\n    membership_mode = _normalize_membership_mode(settings.get("membership_mode") or settings.get("membership") or settings.get("account_type") or settings.get("world_type"))\n    ml_weight = max(0.0, min(1.0, _to_float(settings.get("ml_weight"), 0.0)))\n    metadata = load_item_metadata_cache()\n',
            "membership_mode_setting",
        )

        text = replace_once(
            text,
            '''            "risk_mode": risk_mode,
            "hours_away": hours_away,
            "ml_weight": ml_weight,''',
            '''            "risk_mode": risk_mode,
            "membership_mode": membership_mode,
            "hours_away": hours_away,
            "ml_weight": ml_weight,''',
            "error_response_membership_mode",
        )

        text = replace_once(
            text,
            '''        if buy_price > budget:
            continue
        if confidence not in risk["min_confidence"] or speed not in risk["allowed_speed"]:
            continue''',
            '''        if buy_price > budget:
            continue
        if not _membership_allowed(candidate, membership_mode=membership_mode, metadata=metadata):
            continue
        if confidence not in risk["min_confidence"] or speed not in risk["allowed_speed"]:
            continue''',
            "candidate_membership_filter",
        )

        text = replace_once(
            text,
            '''        "risk_mode": risk_mode,
        "hours_away": hours_away,''',
            '''        "risk_mode": risk_mode,
        "membership_mode": membership_mode,
        "hours_away": hours_away,''',
            "success_response_membership_mode",
        )

        text = replace_once(
            text,
            '''        "item_metadata_count": len(metadata),
        "spent_gp": spent,''',
            '''        "item_metadata_count": len(metadata),
        "membership_filter": {
            "mode": membership_mode,
            "members_items_allowed": membership_mode != "f2p",
        },
        "spent_gp": spent,''',
            "membership_filter_metadata",
        )

        text = replace_once(
            text,
            '''        "category": _category_for_candidate(candidate),
        "buy_limit_4h": buy_limit,''',
            '''        "category": _category_for_candidate(candidate),
        "members": _item_is_members_only(item_id, metadata),
        "buy_limit_4h": buy_limit,''',
            "slot_members_field",
        )

        if text != original:
            backup(TARGET)
            TARGET.write_text(text, encoding="utf-8")

        subprocess.run([sys.executable, "-m", "py_compile", str(TARGET)], check=True)

        print("path:", TARGET)
        print("changed:", text != original)
        print("membership_mode_mentions:", text.count("membership_mode"))
        print("membership_allowed_mentions:", text.count("_membership_allowed"))
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
