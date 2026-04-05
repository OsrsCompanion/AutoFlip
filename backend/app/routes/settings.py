from typing import Any

from fastapi import APIRouter, Request

from app.services.auth_store import get_session
from app.services.settings_store import (
    load_settings,
    normalize_settings_input,
    save_settings,
    to_public_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _extract_token(request: Request) -> str:
    auth = str(request.headers.get("authorization", "") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.query_params.get("token", "") or "").strip()


def _has_valid_session(request: Request) -> bool:
    token = _extract_token(request)
    if not token:
        return False
    try:
        get_session(token)
        return True
    except Exception:
        return False


@router.get("")
def get_settings():
    return to_public_settings(load_settings())


@router.post("")
async def post_settings(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    data: dict[str, Any] = normalize_settings_input(payload)
    saved = save_settings(data, allow_secret_updates=_has_valid_session(request))
    return {"status": "saved", "settings": to_public_settings(saved)}
