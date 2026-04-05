from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.services.auth_store import (
    activate_subscription,
    authenticate_user,
    create_user,
    get_download_payload,
    get_public_plans,
    get_session,
    link_plugin_device,
    list_plugin_devices,
    logout_session,
    unlink_plugin_device,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_token(token: str = "", authorization: str | None = None) -> str:
    explicit = str(token or "").strip()
    if explicit:
        return explicit
    auth = str(authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class SubscribeRequest(BaseModel):
    token: str
    plan_tier: str = "pro"


class PluginLinkRequest(BaseModel):
    token: str
    device_name: str = "RuneLite"


@router.get("/plans")
def plans():
    return {"ok": True, "plans": get_public_plans()}


@router.post("/signup")
def signup(request: SignupRequest):
    try:
        user = create_user(request.email, request.password)
        login_payload = authenticate_user(request.email, request.password, client_type="web")
        return {"ok": True, "user": user, "token": login_payload.get("token")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login")
def login(request: LoginRequest):
    try:
        return {"ok": True, **authenticate_user(request.email, request.password, client_type="web")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me")
def me(token: str = Query(default=""), authorization: str | None = Header(default=None)):
    try:
        return {"ok": True, **get_session(_resolve_token(token, authorization))}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout")
def logout(token: str = Query(default=""), authorization: str | None = Header(default=None)):
    return logout_session(_resolve_token(token, authorization))


@router.post("/subscribe")
def subscribe(request: SubscribeRequest, authorization: str | None = Header(default=None)):
    try:
        user = activate_subscription(_resolve_token(request.token, authorization), request.plan_tier)
        return {"ok": True, "user": user}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plugin/link")
def plugin_link(request: PluginLinkRequest, authorization: str | None = Header(default=None)):
    try:
        return {"ok": True, **link_plugin_device(_resolve_token(request.token, authorization), request.device_name)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/plugin/devices")
def plugin_devices(token: str = Query(default=""), authorization: str | None = Header(default=None)):
    try:
        return {"ok": True, **list_plugin_devices(_resolve_token(token, authorization))}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.delete("/plugin/devices/{device_id}")
def plugin_unlink_device(device_id: str, token: str = Query(default=""), authorization: str | None = Header(default=None)):
    try:
        return unlink_plugin_device(_resolve_token(token, authorization), device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/plugin/download")
def plugin_download(token: str = Query(default=""), authorization: str | None = Header(default=None)):
    try:
        return {"ok": True, **get_download_payload(_resolve_token(token, authorization))}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
