from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.services.board_optimizer import build_board_plan

router = APIRouter(prefix="/api/board", tags=["board"])


async def _payload_from_request(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


@router.post("/plan")
async def post_board_plan(request: Request):
    payload = await _payload_from_request(request)
    try:
        return build_board_plan(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"board planner failed: {exc}") from exc
