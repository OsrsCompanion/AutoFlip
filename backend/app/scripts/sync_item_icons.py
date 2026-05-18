"""
Sync OSRS item icons into the local runtime static folder.

Runtime output:
  /mnt/nvme/autoflip-data/static/item_icons/{item_id}.png

Default behavior:
  Reads /mnt/nvme/autoflip-data/cache/recommendation_candidate_cache.json
  and downloads missing candidate icons only.

All-item behavior:
  Use --all-items to read the OSRS Wiki mapping endpoint and download
  every mapped OSRS item icon, including graph/watchlist-only items.

Examples:
  python3 -m app.scripts.sync_item_icons
  python3 -m app.scripts.sync_item_icons --limit 25
  python3 -m app.scripts.sync_item_icons --all-items
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

APP_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CACHE_PATH = Path("/mnt/nvme/autoflip-data/cache/recommendation_candidate_cache.json")
OSRS_MAPPING_URL = "https://prices.runescape.wiki/api/v1/osrs/mapping"

STATIC_ROOT = Path("/mnt/nvme/autoflip-data/static")
ICON_DIR = STATIC_ROOT / "item_icons"
PLACEHOLDER_PATH = ICON_DIR / "placeholder.png"

RUNELITE_ICON_URL = "https://static.runelite.net/cache/item/icon/{item_id}.png"
WIKI_IMAGE_BASE = "https://oldschool.runescape.wiki/images/{filename}"

USER_AGENT = "AutoFlip item icon sync - local deployment"


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _request_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_candidates(cache_path: Path) -> list[dict[str, Any]]:
    if not cache_path.exists():
        raise FileNotFoundError(f"candidate cache not found: {cache_path}")

    with cache_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        rows = data.get("candidates") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    return [row for row in rows if isinstance(row, dict)]


def _load_all_items_from_mapping(timeout: float) -> list[dict[str, Any]]:
    data = _request_json(OSRS_MAPPING_URL, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError("OSRS mapping endpoint returned unexpected shape")

    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        item_id = _safe_int(row.get("id"))
        name = str(row.get("name") or "").strip()
        if not item_id or not name:
            continue
        rows.append({"item_id": item_id, "name": name})
    return rows


def _dedupe_items(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    out: list[dict[str, Any]] = []

    for row in rows:
        item_id = _safe_int(row.get("item_id") or row.get("id"))
        if not item_id or item_id in seen:
            continue

        name = str(row.get("item_name") or row.get("name") or "").strip()
        if not name:
            continue

        seen.add(item_id)
        out.append({"item_id": item_id, "name": name})

    return out


def _wiki_filename_candidates(name: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", name.strip())
    if not cleaned:
        return []

    base_names = [
        cleaned,
        cleaned.replace(" ", "_"),
    ]

    names: list[str] = []
    for base_name in base_names:
        names.append(f"{base_name}.png")
        names.append(f"{base_name}_detail.png")

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in names:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _download_bytes(url: str, timeout: float) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            payload = response.read()
            if status != 200:
                return None
            if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
            return payload
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


def _icon_urls(item_id: int, name: str) -> list[str]:
    urls = [RUNELITE_ICON_URL.format(item_id=item_id)]
    for filename in _wiki_filename_candidates(name):
        urls.append(WIKI_IMAGE_BASE.format(filename=urllib.parse.quote(filename.replace(" ", "_"))))
    return urls


def ensure_placeholder() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    if not PLACEHOLDER_PATH.exists() or PLACEHOLDER_PATH.stat().st_size <= 0:
        PLACEHOLDER_PATH.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
            b"\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00\x03\x03\x02\x00\xef\xbf\xa7\xdb"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )


def sync_icons(
    cache_path: Path = DEFAULT_CACHE_PATH,
    icon_dir: Path = ICON_DIR,
    *,
    all_items: bool = False,
    limit: int | None = None,
    force: bool = False,
    sleep_seconds: float = 0.03,
    timeout: float = 12.0,
) -> dict[str, int | str]:
    icon_dir.mkdir(parents=True, exist_ok=True)
    ensure_placeholder()

    source = "osrs_mapping" if all_items else "recommendation_candidate_cache"
    if all_items:
        rows = _dedupe_items(_load_all_items_from_mapping(timeout=timeout))
    else:
        rows = _dedupe_items(_load_candidates(cache_path))

    if limit and limit > 0:
        rows = rows[:limit]

    stats: dict[str, int | str] = {
        "source": source,
        "items_seen": len(rows),
        "existing": 0,
        "downloaded": 0,
        "failed": 0,
        "skipped": 0,
    }

    for index, item in enumerate(rows, start=1):
        item_id = int(item["item_id"])
        name = str(item["name"])
        target = icon_dir / f"{item_id}.png"

        if target.exists() and target.stat().st_size > 0 and not force:
            stats["existing"] = int(stats["existing"]) + 1
            continue

        downloaded = False
        for url in _icon_urls(item_id, name):
            payload = _download_bytes(url, timeout=timeout)
            if payload:
                tmp = target.with_suffix(".png.tmp")
                tmp.write_bytes(payload)
                tmp.replace(target)
                stats["downloaded"] = int(stats["downloaded"]) + 1
                downloaded = True
                break

        if not downloaded:
            stats["failed"] = int(stats["failed"]) + 1

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        if index % 100 == 0:
            print(
                f"progress: {index}/{len(rows)} "
                f"downloaded={stats['downloaded']} existing={stats['existing']} failed={stats['failed']}",
                flush=True,
            )

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync local OSRS item icons for AutoFlip UI.")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE_PATH), help="candidate cache JSON path")
    parser.add_argument("--icon-dir", default=str(ICON_DIR), help="output icon directory")
    parser.add_argument("--all-items", action="store_true", help="download icons for all OSRS mapping items")
    parser.add_argument("--limit", type=int, default=0, help="optional max items to process")
    parser.add_argument("--force", action="store_true", help="redownload existing icons")
    parser.add_argument("--sleep", type=float, default=0.03, help="delay between downloads")
    args = parser.parse_args(argv)

    try:
        stats = sync_icons(
            cache_path=Path(args.cache),
            icon_dir=Path(args.icon_dir),
            all_items=bool(args.all_items),
            limit=args.limit or None,
            force=bool(args.force),
            sleep_seconds=float(args.sleep),
        )
        print(json.dumps(stats, indent=2))
        print("icons_dir:", Path(args.icon_dir).resolve())
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
