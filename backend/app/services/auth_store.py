from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from typing import Any

DATA_DIR = "data"
STORE_PATH = os.path.join(DATA_DIR, "auth_store.json")
PLUGIN_DOWNLOAD_URL = "/downloads/osrs-companion-plugin.jar"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _default_store() -> dict[str, Any]:
    return {
        "users": {},
        "sessions": {},
        "subscriptions": {},
        "plugin_links": {},
        "updated_at": "",
    }


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_user_record(email: str, user: Any) -> dict[str, Any] | None:
    if not isinstance(user, dict):
        return None

    normalized_email = _normalize_email(user.get("email") or email)
    if not normalized_email:
        return None

    plugin_links = user.get("plugin_links")
    if not isinstance(plugin_links, list):
        plugin_links = []

    return {
        **user,
        "email": normalized_email,
        "plugin_links": plugin_links,
    }


def _normalize_store(data: Any) -> dict[str, Any]:
    base = _default_store()
    if not isinstance(data, dict):
        return base

    users_raw = data.get("users")
    users: dict[str, Any] = {}
    if isinstance(users_raw, dict):
        for email, user in users_raw.items():
            coerced_user = _coerce_user_record(str(email), user)
            if coerced_user:
                users[coerced_user["email"]] = coerced_user
    elif isinstance(users_raw, list):
        for user in users_raw:
            coerced_user = _coerce_user_record("", user)
            if coerced_user:
                users[coerced_user["email"]] = coerced_user

    plugin_links = _coerce_mapping(data.get("plugin_links"))
    sessions = _coerce_mapping(data.get("sessions"))
    subscriptions = _coerce_mapping(data.get("subscriptions"))

    normalized = {
        **base,
        **data,
        "users": users,
        "sessions": sessions,
        "subscriptions": subscriptions,
        "plugin_links": plugin_links,
    }
    return normalized


def _ensure_store() -> dict[str, Any]:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(STORE_PATH):
        return _default_store()
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return _normalize_store(data)
    except Exception:
        pass
    return _default_store()


def _save_store(store: dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    store["updated_at"] = _utc_now()
    with open(STORE_PATH, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2)


def _normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def _hash_password(password: str, salt: str) -> str:
    raw = f"{salt}:{password}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_public_plans() -> list[dict[str, Any]]:
    return [
        {
            "plan_tier": "free",
            "price_label": "$0",
            "summary": "Discovery mode",
            "features": [
                "Market explorer",
                "Favorites watchlist",
                "Strategy preview",
                "Basic AI explanations",
            ],
        },
        {
            "plan_tier": "pro",
            "price_label": "$5/mo",
            "summary": "Plugin execution mode",
            "features": [
                "Full 8-slot optimization",
                "RuneLite plugin access",
                "Execution-ready recommendations",
                "Player-aware planning",
            ],
        },
    ]


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user.get("user_id"),
        "email": user.get("email"),
        "plan_tier": user.get("plan_tier", "free"),
        "subscription_status": user.get("subscription_status", "inactive"),
        "plugin_access_enabled": bool(user.get("plugin_access_enabled", False)),
        "plugin_download_url": PLUGIN_DOWNLOAD_URL if bool(user.get("plugin_access_enabled", False)) else "",
        "created_at": user.get("created_at", ""),
        "last_login": user.get("last_login", ""),
        "linked_devices": len(user.get("plugin_links", [])),
        "billing": {
            "next_billing_at": user.get("next_billing_at", ""),
            "price_label": "$5/mo" if user.get("plan_tier") == "pro" else "$0",
        },
    }


def create_user(email: str, password: str) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("Valid email is required")
    if len(str(password or "")) < 6:
        raise ValueError("Password must be at least 6 characters")

    store = _ensure_store()
    users = store.setdefault("users", {})
    if normalized_email in users:
        raise ValueError("Account already exists")

    salt = secrets.token_hex(8)
    user = {
        "user_id": secrets.token_hex(8),
        "email": normalized_email,
        "password_salt": salt,
        "password_hash": _hash_password(password, salt),
        "plan_tier": "free",
        "subscription_status": "inactive",
        "plugin_access_enabled": False,
        "plugin_links": [],
        "created_at": _utc_now(),
        "last_login": "",
        "next_billing_at": "",
    }
    users[normalized_email] = user
    _save_store(store)
    return _public_user(user)


def authenticate_user(email: str, password: str, client_type: str = "web", device_name: str = "") -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    store = _ensure_store()
    user = store.get("users", {}).get(normalized_email)
    if not isinstance(user, dict):
        raise ValueError("Invalid email or password")

    expected_hash = _hash_password(password, str(user.get("password_salt") or ""))
    if expected_hash != user.get("password_hash"):
        raise ValueError("Invalid email or password")

    token = secrets.token_urlsafe(32)
    user["last_login"] = _utc_now()
    session = {
        "token": token,
        "user_id": user.get("user_id"),
        "email": user.get("email"),
        "client_type": client_type,
        "device_name": str(device_name or "").strip(),
        "created_at": _utc_now(),
        "last_seen_at": _utc_now(),
    }
    store.setdefault("sessions", {})[token] = session
    _save_store(store)
    return {
        "token": token,
        "user": _public_user(user),
        "client_type": client_type,
    }


def logout_session(token: str) -> dict[str, Any]:
    store = _ensure_store()
    removed = store.setdefault("sessions", {}).pop(str(token or ""), None)
    _save_store(store)
    return {"ok": removed is not None}


def get_session(token: str) -> dict[str, Any]:
    token = str(token or "").strip()
    if not token:
        raise ValueError("token is required")

    store = _ensure_store()
    session = store.get("sessions", {}).get(token)
    if not isinstance(session, dict):
        raise ValueError("Invalid or expired token")

    email = _normalize_email(session.get("email", ""))
    user = store.get("users", {}).get(email)
    if not isinstance(user, dict):
        raise ValueError("User not found")

    session["last_seen_at"] = _utc_now()
    _save_store(store)
    return {
        "token": token,
        "session": session,
        "user": _public_user(user),
    }


def activate_subscription(token: str, plan_tier: str = "pro") -> dict[str, Any]:
    token = str(token or "").strip()
    session_payload = get_session(token)
    email = _normalize_email(session_payload["user"]["email"])

    store = _ensure_store()
    user = store.get("users", {}).get(email)
    if not isinstance(user, dict):
        raise ValueError("User not found")

    user["plan_tier"] = plan_tier or "pro"
    user["subscription_status"] = "active"
    user["plugin_access_enabled"] = True
    user["next_billing_at"] = "2026-04-28T00:00:00+00:00"
    plugin_links = user.get("plugin_links")
    if not isinstance(plugin_links, list):
        plugin_links = []
    if not plugin_links:
        plugin_links.append({
            "device_id": secrets.token_hex(6),
            "device_name": "RuneLite",
            "linked_at": _utc_now(),
            "status": "linked",
        })
    user["plugin_links"] = plugin_links
    store.setdefault("subscriptions", {})[user["user_id"]] = {
        "user_id": user["user_id"],
        "plan_tier": user["plan_tier"],
        "subscription_status": user["subscription_status"],
        "created_at": user.get("created_at", _utc_now()),
        "updated_at": _utc_now(),
    }
    _save_store(store)
    return _public_user(user)


def list_plugin_devices(token: str) -> dict[str, Any]:
    payload = get_session(token)
    email = _normalize_email(payload["user"]["email"])
    store = _ensure_store()
    user = store.get("users", {}).get(email)
    if not isinstance(user, dict):
        raise ValueError("User not found")
    return {
        "user": _public_user(user),
        "devices": list(user.get("plugin_links", [])),
    }


def link_plugin_device(token: str, device_name: str) -> dict[str, Any]:
    payload = get_session(token)
    email = _normalize_email(payload["user"]["email"])
    store = _ensure_store()
    user = store.get("users", {}).get(email)
    if not isinstance(user, dict):
        raise ValueError("User not found")

    device_label = str(device_name or "RuneLite").strip() or "RuneLite"
    for existing in user.setdefault("plugin_links", []):
        if str(existing.get("device_name", "")).strip().lower() == device_label.lower():
            existing["last_seen_at"] = _utc_now()
            _save_store(store)
            return {"user": _public_user(user), "device": existing}

    device = {
        "device_id": secrets.token_hex(6),
        "device_name": device_label,
        "linked_at": _utc_now(),
        "last_seen_at": _utc_now(),
    }
    user.setdefault("plugin_links", []).append(device)
    store.setdefault("plugin_links", {})[device["device_id"]] = {
        "user_id": user.get("user_id"),
        **device,
    }
    _save_store(store)
    return {"user": _public_user(user), "device": device}


def touch_plugin_device(token: str, device_name: str = "") -> dict[str, Any]:
    payload = get_session(token)
    email = _normalize_email(payload["user"]["email"])
    store = _ensure_store()
    user = store.get("users", {}).get(email)
    if not isinstance(user, dict):
        raise ValueError("User not found")

    selected_name = str(device_name or "").strip().lower()
    target = None
    for device in user.setdefault("plugin_links", []):
        if selected_name and str(device.get("device_name", "")).strip().lower() != selected_name:
            continue
        target = device
        break

    if target is None:
        device_label = str(device_name or "RuneLite").strip() or "RuneLite"
        target = {
            "device_id": secrets.token_hex(6),
            "device_name": device_label,
            "linked_at": _utc_now(),
            "last_seen_at": _utc_now(),
        }
        user.setdefault("plugin_links", []).append(target)

    target["last_seen_at"] = _utc_now()
    store.setdefault("plugin_links", {})[target["device_id"]] = {
        "user_id": user.get("user_id"),
        **target,
    }
    _save_store(store)
    return {"user": _public_user(user), "device": target}


def unlink_plugin_device(token: str, device_id: str) -> dict[str, Any]:
    payload = get_session(token)
    email = _normalize_email(payload["user"]["email"])
    store = _ensure_store()
    user = store.get("users", {}).get(email)
    if not isinstance(user, dict):
        raise ValueError("User not found")

    device_id = str(device_id or "").strip()
    if not device_id:
        raise ValueError("device_id is required")

    devices = user.setdefault("plugin_links", [])
    remaining = [device for device in devices if str(device.get("device_id", "")) != device_id]
    removed = len(remaining) != len(devices)
    user["plugin_links"] = remaining
    store.setdefault("plugin_links", {}).pop(device_id, None)
    _save_store(store)
    return {"ok": removed, "user": _public_user(user), "devices": remaining}


def get_download_payload(token: str) -> dict[str, Any]:
    payload = get_session(token)
    user = payload.get("user", {})
    return {
        "user": user,
        "download_url": PLUGIN_DOWNLOAD_URL if user.get("plugin_access_enabled") else "",
        "plugin_locked": not bool(user.get("plugin_access_enabled")),
    }


def require_plugin_access(token: str) -> dict[str, Any]:
    payload = get_session(token)
    user = payload.get("user", {})
    if not bool(user.get("plugin_access_enabled")):
        raise ValueError("Plugin access requires an active Pro subscription")
    return payload
