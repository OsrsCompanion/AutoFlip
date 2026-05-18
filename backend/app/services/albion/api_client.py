from __future__ import annotations

import gzip
import json
import urllib.parse
import urllib.request
from typing import Any

from .config import SERVERS


class AlbionApiError(RuntimeError):
    pass


def fetch_prices(
    *,
    server: str,
    item_ids: list[str] | tuple[str, ...],
    locations: list[str] | tuple[str, ...],
    qualities: list[int] | tuple[int, ...],
    timeout_seconds: int = 30,
) -> list[dict[str, Any]]:
    """Fetch current market prices from Albion Online Data Project.

    Uses gzip per AODP guidance for continually running services.
    """
    server_key = str(server).lower().strip()
    base_url = SERVERS.get(server_key)
    if not base_url:
        raise AlbionApiError(f"unsupported Albion server={server!r}")

    clean_items = [str(x).strip() for x in item_ids if str(x).strip()]
    if not clean_items:
        return []

    query = urllib.parse.urlencode({
        "locations": ",".join(str(x).strip() for x in locations if str(x).strip()),
        "qualities": ",".join(str(int(x)) for x in qualities),
    })
    item_path = ",".join(urllib.parse.quote(x, safe="") for x in clean_items)
    url = f"{base_url}/api/v2/stats/prices/{item_path}.json?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "AutoFlipGG-AlbionCollector/0.1 (+raw market research)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # keep runner alive; caller logs details
        raise AlbionApiError(str(exc)) from exc

    if not isinstance(payload, list):
        raise AlbionApiError(f"unexpected API payload type={type(payload).__name__}")
    return [row for row in payload if isinstance(row, dict)]
