from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.ai_advisor import build_ai_advice
from app.services.player_state import (
    append_player_events,
    get_player_alerts,
    get_player_state,
    upsert_player_ge_slots,
    upsert_player_holdings,
    upsert_player_preferences,
    upsert_player_session,
)

router = APIRouter(prefix="/api/player", tags=["player"])


class PlayerAIRequest(BaseModel):
    player_id: str
    message: str = ""


async def _payload_from_request(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


@router.post("/session")
async def post_player_session(request: Request):
    payload = await _payload_from_request(request)
    try:
        player = upsert_player_session(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "player": player}


@router.post("/ge-slots")
async def post_player_ge_slots(request: Request):
    payload = await _payload_from_request(request)
    try:
        player = upsert_player_ge_slots(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "player": player}


@router.put("/preferences")
async def put_player_preferences(request: Request, player_id: str = Query(default="")):
    payload = await _payload_from_request(request)
    effective_player_id = player_id or str(payload.get("player_id", "") or "")
    try:
        player = upsert_player_preferences(effective_player_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "player": player}


@router.post("/holdings")
async def post_player_holdings(request: Request):
    payload = await _payload_from_request(request)
    try:
        player = upsert_player_holdings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "player": player}


@router.post("/events")
async def post_player_events(request: Request):
    payload = await _payload_from_request(request)
    try:
        player = append_player_events(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "player": player}


@router.get("/state")
def read_player_state(player_id: str = Query(default="")):
    try:
        return get_player_state(player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/alerts")
def read_player_alerts(player_id: str = Query(default="")):
    try:
        return get_player_alerts(player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/advise")
def player_ai_advise(request: PlayerAIRequest):
    try:
        state = get_player_state(request.player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    context_prefix = (
        f"Player state for {request.player_id}: "
        f"status={state.get('session', {}).get('status', '')}; "
        f"cash={state.get('session', {}).get('cash_stack', 0)}; "
        f"active_ge_slots={len(state.get('ge_slots', []))}; "
        f"holdings={len(state.get('holdings', []))}; "
        f"alerts={len(state.get('alerts', []))}. "
    )
    enriched_message = f"{context_prefix}\n\n{request.message.strip()}".strip()
    return build_ai_advice(user_message=enriched_message)
