from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.auth_store import authenticate_user, require_plugin_access, touch_plugin_device
from app.services.player_state import (
    append_player_events,
    build_player_settings_overlay,
    get_player_state,
    summarize_player_state,
    upsert_player_ge_slots,
    upsert_player_holdings,
    upsert_player_preferences,
    upsert_player_session,
)
from app.services.board_optimizer import build_board_plan
from app.services.recommendations import build_recommendations
from app.services.settings_store import load_settings

router = APIRouter(prefix="/api/plugin", tags=["plugin"])


class PluginLoginRequest(BaseModel):
    email: str
    password: str
    device_name: str = "RuneLite"


class PluginOptimizeRequest(BaseModel):
    token: str
    player_id: str | None = None
    slots_available: int | None = None
    budget: int | str | None = None
    hours_away: int | None = None
    risk_profile: str | None = None


class PluginSyncRequest(BaseModel):
    token: str
    player_id: str
    device_name: str = "RuneLite"
    session: dict[str, Any] | None = None
    ge_slots: list[dict[str, Any]] | None = None
    holdings: list[dict[str, Any]] | None = None
    preferences: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None


def _resolve_token(explicit_token: str = "", authorization: str | None = None) -> str:
    token = str(explicit_token or "").strip()
    if token:
        return token
    auth_header = str(authorization or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return auth_header


@router.post("/session/login")
def plugin_login(request: PluginLoginRequest):
    try:
        return {"ok": True, **authenticate_user(request.email, request.password, client_type="plugin", device_name=request.device_name)}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/status")
def plugin_status(token: str = Query(default=""), authorization: str | None = Header(default=None)):
    try:
        payload = require_plugin_access(_resolve_token(token, authorization))
        return {"ok": True, **payload}
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/recommendations")
def plugin_recommendations(
    token: str = Query(default=""),
    player_id: str = Query(default=""),
    limit: int = Query(default=8, ge=1, le=8),
    authorization: str | None = Header(default=None),
):
    try:
        payload = require_plugin_access(_resolve_token(token, authorization))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    settings = load_settings()
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


@router.post("/optimize")
def plugin_optimize(request: PluginOptimizeRequest, authorization: str | None = Header(default=None)):
    try:
        payload = require_plugin_access(_resolve_token(request.token, authorization))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    settings = load_settings().copy()
    if request.player_id:
        settings = build_player_settings_overlay(request.player_id, settings)
    if request.slots_available is not None:
        settings["available_slots"] = request.slots_available
        settings["slots"] = request.slots_available
    if request.budget is not None:
        settings["budget"] = request.budget
    if request.hours_away is not None:
        settings["hours_away"] = request.hours_away
    if request.risk_profile:
        settings["risk_mode"] = request.risk_profile
        settings["risk_profile"] = request.risk_profile

    board_plan = build_board_plan(settings=settings)
    steps = []
    for row in board_plan.get("board", []):
        steps.append(
            {
                "slot_index": row.get("slot"),
                "item_id": row.get("item_id"),
                "item_name": row.get("item_name"),
                "action": "Place buy offer",
                "buy_price": row.get("buy_price"),
                "sell_price": row.get("sell_price"),
                "quantity": row.get("quantity"),
                "capital_required": row.get("capital_required"),
                "target_profit": row.get("expected_profit"),
                "confidence_band": row.get("confidence_band"),
                "fill_speed_band": row.get("fill_speed_band"),
                "reason": row.get("short_reason"),
            }
        )

    return {
        "ok": board_plan.get("status") in {"ok", "empty"},
        "plugin_ready": True,
        "user": payload.get("user"),
        "player_summary": summarize_player_state(request.player_id) if request.player_id else None,
        "plan_summary": {
            "plan_type": board_plan.get("plan_type"),
            "slots_requested": board_plan.get("slots_requested"),
            "slots_filled": board_plan.get("slots_filled"),
            "steps": len(steps),
            "budget": board_plan.get("budget"),
            "spent_gp": board_plan.get("spent_gp"),
            "unallocated_gp": board_plan.get("unallocated_gp"),
            "expected_profit": board_plan.get("expected_profit"),
            "risk_mode": board_plan.get("risk_mode"),
            "hours_away": board_plan.get("hours_away"),
            "ml_weight": board_plan.get("ml_weight"),
        },
        "steps": steps,
        "board_plan": board_plan,
        "top_candidates": board_plan.get("board", []),
    }

@router.get("/player-state")
def plugin_player_state(
    token: str = Query(default=""),
    player_id: str = Query(default=""),
    authorization: str | None = Header(default=None),
):
    try:
        payload = require_plugin_access(_resolve_token(token, authorization))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        state = get_player_state(player_id)
        summary = summarize_player_state(player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "user": payload.get("user"),
        "player_summary": summary,
        "player_state": state,
    }


@router.post("/sync/full")
def plugin_sync_full(request: PluginSyncRequest, authorization: str | None = Header(default=None)):
    try:
        payload = require_plugin_access(_resolve_token(request.token, authorization))
        touch_plugin_device(_resolve_token(request.token, authorization), request.device_name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    player_id = str(request.player_id or "").strip()
    if not player_id:
        raise HTTPException(status_code=400, detail="player_id is required")

    if request.session is not None:
        session_payload = dict(request.session)
        session_payload["player_id"] = player_id
        upsert_player_session(session_payload)

    if request.ge_slots is not None:
        upsert_player_ge_slots({"player_id": player_id, "slots": request.ge_slots, "synced_at": (request.session or {}).get("synced_at")})

    if request.holdings is not None:
        upsert_player_holdings({"player_id": player_id, "holdings": request.holdings, "synced_at": (request.session or {}).get("synced_at")})

    if request.preferences is not None:
        upsert_player_preferences(player_id, request.preferences)

    if request.events:
        append_player_events({"player_id": player_id, "events": request.events})

    settings = build_player_settings_overlay(player_id, load_settings())
    board_plan = build_board_plan(settings=settings)

    return {
        "ok": True,
        "plugin_ready": True,
        "user": payload.get("user"),
        "device_name": request.device_name,
        "player_summary": summarize_player_state(player_id),
        "player_state": get_player_state(player_id),
        "recommendations": board_plan.get("board", []),
        "board_plan": board_plan,
        "plan_summary": {
            "slots_available": board_plan.get("slots_requested", settings.get("available_slots", 0)),
            "slots_filled": board_plan.get("slots_filled", 0),
            "budget": board_plan.get("budget", settings.get("budget", 0)),
            "spent_gp": board_plan.get("spent_gp", 0),
            "unallocated_gp": board_plan.get("unallocated_gp", 0),
            "expected_profit": board_plan.get("expected_profit", 0),
            "risk_profile": board_plan.get("risk_mode", settings.get("risk_profile", "balanced")),
            "hours_away": board_plan.get("hours_away", settings.get("hours_away", 0)),
            "ml_weight": board_plan.get("ml_weight", 0),
        },
    }


@router.post("/heartbeat")
async def plugin_heartbeat(request: Request, authorization: str | None = Header(default=None)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = _resolve_token(str(body.get("token", "")), authorization)
    device_name = str(body.get("device_name", "RuneLite") or "RuneLite")
    try:
        payload = require_plugin_access(token)
        linked = touch_plugin_device(token, device_name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "ok": True,
        "user": payload.get("user"),
        "device": linked.get("device"),
    }
