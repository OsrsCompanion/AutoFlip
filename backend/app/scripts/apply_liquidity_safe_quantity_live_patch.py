from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

TARGETS = {
    "collector": ROOT / "app" / "services" / "market_intelligence_cache.py",
    "ui": ROOT / "app" / "ui" / "app" / "index.html",
}


def backup(path: Path) -> Path:
    backup_path = path.with_name(f"{path.name}.bak_liquidity_safe_quantity_{STAMP}")
    shutil.copy2(path, backup_path)
    return backup_path


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if old not in text:
        return text, False
    return text.replace(old, new, 1), True


def replace_all(text: str, old: str, new: str) -> tuple[str, int]:
    count = text.count(old)
    if count:
        text = text.replace(old, new)
    return text, count


def patch_collector(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    original = text

    old = '        "suggested_quantity": max_qty,\n        "max_quantity": max_qty,\n'
    new = (
        '        "liquidity_safe_quantity": max_qty,\n'
        '        "suggested_quantity": max_qty,\n'
        '        "max_quantity": max_qty,\n'
    )
    text, changed = replace_once(text, old, new, "collector_return_block")

    if not changed and '"liquidity_safe_quantity": max_qty' not in text:
        raise RuntimeError("collector patch target not found")

    if text != original:
        backup(path)
        path.write_text(text, encoding="utf-8")

    return {"path": str(path), "changed": text != original, "has_field": '"liquidity_safe_quantity": max_qty' in text}


def patch_ui(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    original = text
    counts: dict[str, int] = {}

    replacements = [
        (
            "item.buy_limit || item.suggested_quantity || 0",
            "item.buy_limit || item.liquidity_safe_quantity || item.suggested_quantity || 0",
        ),
        (
            "item.allocated_quantity || item.suggested_quantity || 0",
            "item.allocated_quantity || item.liquidity_safe_quantity || item.suggested_quantity || 0",
        ),
        (
            "nextTrade.allocated_quantity || nextTrade.buy_limit || nextTrade.suggested_quantity || 0",
            "nextTrade.allocated_quantity || nextTrade.liquidity_safe_quantity || nextTrade.buy_limit || nextTrade.suggested_quantity || 0",
        ),
        (
            "selectedItem?.allocated_quantity || selectedItem?.buy_limit || selectedItem?.suggested_quantity || 0",
            "selectedItem?.allocated_quantity || selectedItem?.liquidity_safe_quantity || selectedItem?.buy_limit || selectedItem?.suggested_quantity || 0",
        ),
        (
            "appliedTrade.buy_limit || appliedTrade.suggested_quantity || 0",
            "appliedTrade.allocated_quantity || appliedTrade.liquidity_safe_quantity || appliedTrade.buy_limit || appliedTrade.suggested_quantity || 0",
        ),
        (
            "ghost.allocated_quantity || ghost.buy_limit || ghost.suggested_quantity || 0",
            "ghost.allocated_quantity || ghost.liquidity_safe_quantity || ghost.buy_limit || ghost.suggested_quantity || 0",
        ),
        (
            "ghost.allocated_quantity || ghost.buy_limit || 0",
            "ghost.allocated_quantity || ghost.liquidity_safe_quantity || ghost.buy_limit || 0",
        ),
    ]

    for old, new in replacements:
        text, count = replace_all(text, old, new)
        counts[old] = count

    old_obj = "          suggested_quantity: quantity,\n          allocated_quantity: quantity,\n"
    new_obj = (
        "          liquidity_safe_quantity: quantity,\n"
        "          suggested_quantity: quantity,\n"
        "          allocated_quantity: quantity,\n"
    )
    text, count = replace_all(text, old_obj, new_obj)
    counts["object_liquidity_safe_quantity"] = count

    if text != original:
        backup(path)
        path.write_text(text, encoding="utf-8")

    return {
        "path": str(path),
        "changed": text != original,
        "liquidity_safe_quantity_mentions": text.count("liquidity_safe_quantity"),
        "replacement_counts": counts,
    }


def run_py_compile(path: Path) -> None:
    subprocess.run([sys.executable, "-m", "py_compile", str(path)], check=True)


def main() -> int:
    try:
        for name, path in TARGETS.items():
            if not path.exists():
                raise FileNotFoundError(f"{name} target missing: {path}")

        collector_result = patch_collector(TARGETS["collector"])
        ui_result = patch_ui(TARGETS["ui"])

        run_py_compile(TARGETS["collector"])

        print("collector:", collector_result)
        print("ui:", ui_result)
        print()
        print("NEXT:")
        print("Restart web and collector services so both runtimes load the updated files.")
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
