from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

COLLECTOR = ROOT / "app" / "services" / "market_intelligence_cache.py"
UI = ROOT / "app" / "ui" / "app" / "index.html"


def backup(path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.bak_liquidity_safe_quantity_cleanup_{STAMP}")
    shutil.copy2(path, backup_path)
    return backup_path


def patch_file(path: Path, replacements: list[tuple[str, str]]) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    original = text
    counts: dict[str, int] = {}

    for old, new in replacements:
        count = text.count(old)
        if count:
            text = text.replace(old, new)
        counts[old[:80]] = count

    if text != original:
        backup(path)
        path.write_text(text, encoding="utf-8")

    return {"path": str(path), "changed": text != original, "counts": counts}


def main() -> int:
    try:
        collector_replacements = [
            (
                '        "liquidity_safe_quantity": max_qty,\n'
                '        "liquidity_safe_quantity": max_qty,\n',
                '        "liquidity_safe_quantity": max_qty,\n',
            ),
        ]

        ui_replacements = [
            (
                '          liquidity_safe_quantity: quantity,\n'
                '          liquidity_safe_quantity: quantity,\n',
                '          liquidity_safe_quantity: quantity,\n',
            ),
            (
                'appliedTrade.allocated_quantity || appliedTrade.liquidity_safe_quantity || '
                'appliedTrade.allocated_quantity || appliedTrade.liquidity_safe_quantity || '
                'appliedTrade.buy_limit || appliedTrade.suggested_quantity || 0',
                'appliedTrade.allocated_quantity || appliedTrade.liquidity_safe_quantity || '
                'appliedTrade.buy_limit || appliedTrade.suggested_quantity || 0',
            ),
        ]

        collector_result = patch_file(COLLECTOR, collector_replacements)
        ui_result = patch_file(UI, ui_replacements)

        subprocess.run([sys.executable, "-m", "py_compile", str(COLLECTOR)], check=True)

        collector_text = COLLECTOR.read_text(encoding="utf-8")
        ui_text = UI.read_text(encoding="utf-8")

        print("collector:", collector_result)
        print("ui:", ui_result)
        print("collector_mentions:", collector_text.count("liquidity_safe_quantity"))
        print("ui_mentions:", ui_text.count("liquidity_safe_quantity"))
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
