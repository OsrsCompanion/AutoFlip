from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
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
def me(token: str = Query(default="")):
    try:
        return {"ok": True, **get_session(token)}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout")
def logout(token: str = Query(default="")):
    return logout_session(token)


@router.post("/subscribe")
def subscribe(request: SubscribeRequest):
    try:
        user = activate_subscription(request.token, request.plan_tier)
        return {"ok": True, "user": user}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plugin/link")
def plugin_link(request: PluginLinkRequest):
    try:
        return {"ok": True, **link_plugin_device(request.token, request.device_name)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/plugin/devices")
def plugin_devices(token: str = Query(default="")):
    try:
        return {"ok": True, **list_plugin_devices(token)}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.delete("/plugin/devices/{device_id}")
def plugin_unlink_device(device_id: str, token: str = Query(default="")):
    try:
        return unlink_plugin_device(token, device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/plugin/download")
def plugin_download(token: str = Query(default="")):
    try:
        return {"ok": True, **get_download_payload(token)}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
