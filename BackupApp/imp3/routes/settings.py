from typing import Any

from fastapi import APIRouter, Request

from app.services.settings_store import load_settings, normalize_settings_input, save_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings():
    return load_settings()


@router.post("")
async def post_settings(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    data: dict[str, Any] = normalize_settings_input(payload)
    save_settings(data)
    return {"status": "saved", "settings": data}
