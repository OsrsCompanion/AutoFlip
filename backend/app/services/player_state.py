from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

PLAYER_DATA_DIR = "data"
PLAYER_STATE_PATH = os.path.join(PLAYER_DATA_DIR, "player_state.json")

DEFAULT_PREFERENCES = {
    "budget": 0,
    "slots_available": 0,
    "hours_away": 0,
    "risk_profile": "medium",
    "play_style": "manual",
    "favorite_item_ids": [],
    "watch_item_ids": [],
    "notification_settings": {
        "browser_notifications": False,
        "chat_alerts": True,
        "urgent_only": False,
    },
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_str(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_item_id(value: Any) -> int | None:
    try:
        item_id = int(value)
    except Exception:
        return None
    return item_id if item_id > 0 else None


def _normalize_notification_settings(value: Any) -> dict[str, bool]:
    base = DEFAULT_PREFERENCES["notification_settings"].copy()
    if not isinstance(value, dict):
        return base
    for key in base.keys():
        if key in value:
            base[key] = bool(value.get(key))
    return base


def _normalize_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    items: list[int] = []
    for raw in value:
        item_id = _normalize_item_id(raw)
        if item_id is not None:
            items.append(item_id)
    return items


def _normalize_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    preferences = DEFAULT_PREFERENCES.copy()
    preferences["notification_settings"] = DEFAULT_PREFERENCES["notification_settings"].copy()
    if not isinstance(payload, dict):
        return preferences

    preferences["budget"] = _safe_int(payload.get("budget", 0), 0)
    preferences["slots_available"] = _safe_int(payload.get("slots_available", 0), 0)
    preferences["hours_away"] = _safe_int(payload.get("hours_away", 0), 0)
    preferences["risk_profile"] = _safe_str(payload.get("risk_profile"), "medium")
    preferences["play_style"] = _safe_str(payload.get("play_style"), "manual")
    preferences["favorite_item_ids"] = _normalize_int_list(payload.get("favorite_item_ids", []))
    preferences["watch_item_ids"] = _normalize_int_list(payload.get("watch_item_ids", []))
    preferences["notification_settings"] = _normalize_notification_settings(payload.get("notification_settings"))
    return preferences


def _normalize_session(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    return {
        "player_id": _safe_str(payload.get("player_id")),
        "session_id": _safe_str(payload.get("session_id")),
        "display_name": _safe_str(payload.get("display_name")),
        "status": _safe_str(payload.get("status"), "offline"),
        "activity": _safe_str(payload.get("activity")),
        "world": _safe_int(payload.get("world", 0), 0),
        "cash_stack": _safe_int(payload.get("cash_stack", 0), 0),
        "inventory_value": _safe_int(payload.get("inventory_value", 0), 0),
        "bank_value": _safe_int(payload.get("bank_value", 0), 0),
        "synced_at": _safe_str(payload.get("synced_at"), _utc_now()),
    }


def _normalize_ge_slot(slot: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(slot, dict):
        return None
    item_id = _normalize_item_id(slot.get("item_id"))
    return {
        "slot_index": _safe_int(slot.get("slot_index", 0), 0),
        "state": _safe_str(slot.get("state")),
        "item_id": item_id,
        "item_name": _safe_str(slot.get("item_name")),
        "price": _safe_int(slot.get("price", 0), 0),
        "quantity_total": _safe_int(slot.get("quantity_total", 0), 0),
        "quantity_filled": _safe_int(slot.get("quantity_filled", 0), 0),
        "spent_or_received": _safe_int(slot.get("spent_or_received", 0), 0),
        "status_text": _safe_str(slot.get("status_text")),
    }


def _normalize_holding(holding: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(holding, dict):
        return None
    item_id = _normalize_item_id(holding.get("item_id"))
    return {
        "item_id": item_id,
        "item_name": _safe_str(holding.get("item_name")),
        "quantity": _safe_int(holding.get("quantity", 0), 0),
        "avg_cost": _safe_int(holding.get("avg_cost", 0), 0),
    }


def _normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    normalized = {
        "type": _safe_str(event.get("type"), "unknown"),
        "item_id": _normalize_item_id(event.get("item_id")),
        "item_name": _safe_str(event.get("item_name")),
        "side": _safe_str(event.get("side")),
        "quantity": _safe_int(event.get("quantity", 0), 0),
        "price": _safe_int(event.get("price", 0), 0),
        "message": _safe_str(event.get("message")),
        "ts": _safe_str(event.get("ts"), _utc_now()),
    }
    event_id = _safe_str(event.get("event_id"))
    if event_id:
        normalized["event_id"] = event_id
    return normalized


def _default_player(player_id: str) -> dict[str, Any]:
    return {
        "player_id": player_id,
        "profile": {
            "player_id": player_id,
            "display_name": "",
        },
        "session": _normalize_session({"player_id": player_id}),
        "preferences": _normalize_preferences({}),
        "ge_slots": [],
        "holdings": [],
        "events": [],
        "alerts": [],
        "last_sync": "",
        "updated_at": _utc_now(),
    }


def _ensure_store() -> dict[str, Any]:
    os.makedirs(PLAYER_DATA_DIR, exist_ok=True)
    if not os.path.exists(PLAYER_STATE_PATH):
        return {"players": {}, "updated_at": ""}
    try:
        with open(PLAYER_STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get("players"), dict):
            return data
    except Exception:
        pass
    return {"players": {}, "updated_at": ""}


def _save_store(store: dict[str, Any]) -> None:
    os.makedirs(PLAYER_DATA_DIR, exist_ok=True)
    store["updated_at"] = _utc_now()
    with open(PLAYER_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2)


def _get_player_record(store: dict[str, Any], player_id: str) -> dict[str, Any]:
    players = store.setdefault("players", {})
    if player_id not in players or not isinstance(players[player_id], dict):
        players[player_id] = _default_player(player_id)
    return players[player_id]


def _derive_alerts(player: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    session = player.get("session", {})
    ge_slots = player.get("ge_slots", [])
    holdings = player.get("holdings", [])
    last_sync = player.get("last_sync") or session.get("synced_at") or ""

    if ge_slots:
        stalled = [slot for slot in ge_slots if slot.get("status_text") and str(slot.get("status_text")).lower() not in {"completed", "finished"}]
        alerts.append(
            {
                "id": "ge_slots_active",
                "severity": "info",
                "type": "state_summary",
                "title": f"{len(stalled)} active GE slot(s)",
                "message": "Player state sync is connected and tracking current GE offers.",
                "created_at": last_sync or _utc_now(),
                "read": False,
            }
        )

    if holdings:
        alerts.append(
            {
                "id": "holdings_loaded",
                "severity": "info",
                "type": "portfolio_summary",
                "title": f"{len(holdings)} holding(s) loaded",
                "message": "Website can now evaluate alerts against current exposure.",
                "created_at": last_sync or _utc_now(),
                "read": False,
            }
        )

    if session.get("status") in {"offline", "disconnected"}:
        alerts.append(
            {
                "id": "session_offline",
                "severity": "low",
                "type": "session_status",
                "title": "Player session offline",
                "message": "Latest sync says the player is offline or disconnected.",
                "created_at": last_sync or _utc_now(),
                "read": False,
            }
        )

    return alerts[:10]


def upsert_player_session(payload: dict[str, Any]) -> dict[str, Any]:
    session = _normalize_session(payload)
    player_id = session.get("player_id")
    if not player_id:
        raise ValueError("player_id is required")

    store = _ensure_store()
    player = _get_player_record(store, player_id)
    player["session"] = session
    player["profile"]["player_id"] = player_id
    player["profile"]["display_name"] = session.get("display_name") or player["profile"].get("display_name", "")
    player["last_sync"] = session.get("synced_at") or _utc_now()
    player["updated_at"] = _utc_now()
    player["alerts"] = _derive_alerts(player)
    _save_store(store)
    return player


def upsert_player_ge_slots(payload: dict[str, Any]) -> dict[str, Any]:
    player_id = _safe_str(payload.get("player_id"))
    if not player_id:
        raise ValueError("player_id is required")

    slots: list[dict[str, Any]] = []
    for raw_slot in payload.get("slots", []):
        slot = _normalize_ge_slot(raw_slot)
        if slot is not None:
            slots.append(slot)

    store = _ensure_store()
    player = _get_player_record(store, player_id)
    player["ge_slots"] = sorted(slots, key=lambda slot: slot.get("slot_index", 0))
    synced_at = _safe_str(payload.get("synced_at"), _utc_now())
    player["last_sync"] = synced_at
    player["updated_at"] = _utc_now()
    player["alerts"] = _derive_alerts(player)
    _save_store(store)
    return player


def upsert_player_preferences(player_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    player_id = _safe_str(player_id)
    if not player_id:
        raise ValueError("player_id is required")

    store = _ensure_store()
    player = _get_player_record(store, player_id)
    player["preferences"] = _normalize_preferences(payload)
    player["updated_at"] = _utc_now()
    player["alerts"] = _derive_alerts(player)
    _save_store(store)
    return player


def upsert_player_holdings(payload: dict[str, Any]) -> dict[str, Any]:
    player_id = _safe_str(payload.get("player_id"))
    if not player_id:
        raise ValueError("player_id is required")

    holdings: list[dict[str, Any]] = []
    for raw_holding in payload.get("holdings", []):
        holding = _normalize_holding(raw_holding)
        if holding is not None:
            holdings.append(holding)

    store = _ensure_store()
    player = _get_player_record(store, player_id)
    player["holdings"] = holdings
    synced_at = _safe_str(payload.get("synced_at"), _utc_now())
    player["last_sync"] = synced_at
    player["updated_at"] = _utc_now()
    player["alerts"] = _derive_alerts(player)
    _save_store(store)
    return player


def append_player_events(payload: dict[str, Any]) -> dict[str, Any]:
    player_id = _safe_str(payload.get("player_id"))
    if not player_id:
        raise ValueError("player_id is required")

    store = _ensure_store()
    player = _get_player_record(store, player_id)
    normalized_events: list[dict[str, Any]] = []
    for raw_event in payload.get("events", []):
        event = _normalize_event(raw_event)
        if event is not None:
            normalized_events.append(event)

    existing = player.get("events", [])
    player["events"] = (normalized_events + existing)[:200]
    player["updated_at"] = _utc_now()
    player["alerts"] = _derive_alerts(player)
    _save_store(store)
    return player


def get_player_state(player_id: str) -> dict[str, Any]:
    player_id = _safe_str(player_id)
    if not player_id:
        raise ValueError("player_id is required")

    store = _ensure_store()
    player = _get_player_record(store, player_id)
    player["alerts"] = _derive_alerts(player)
    return {
        "profile": player.get("profile", {}),
        "session": player.get("session", {}),
        "preferences": player.get("preferences", {}),
        "ge_slots": player.get("ge_slots", []),
        "holdings": player.get("holdings", []),
        "events": player.get("events", [])[:50],
        "alerts": player.get("alerts", []),
        "last_sync": player.get("last_sync", ""),
        "updated_at": player.get("updated_at", ""),
    }


def get_player_alerts(player_id: str) -> dict[str, Any]:
    state = get_player_state(player_id)
    return {
        "player_id": _safe_str(player_id),
        "last_sync": state.get("last_sync", ""),
        "alerts": state.get("alerts", []),
    }
