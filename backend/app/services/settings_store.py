import json
import os
import re
from typing import Any

SETTINGS_DIR = "data"
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")
ALLOWED_HOURS_AWAY = {0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48}

DEFAULT_SETTINGS = {
    "openai_api_key": "",
    "openai_api_key_path": "",
    "openai_model": "gpt-5.4-mini",
    "budget": 10000000,
    "available_slots": 1,
    "hours_away": 0,
}

SECRET_SETTING_FIELDS = {"openai_api_key", "openai_api_key_path"}

ALLOWED_MODELS = {
    "gpt-5.4-mini",
    "gpt-5.4",
}


def parse_budget_value(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().lower().replace(",", "")
    if not text:
        return 0

    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", text)
    if not match:
        try:
            return int(float(text))
        except Exception:
            return 0

    number = float(match.group(1))
    suffix = match.group(2)

    multiplier = 1
    if suffix == "k":
        multiplier = 1_000
    elif suffix == "m":
        multiplier = 1_000_000
    elif suffix == "b":
        multiplier = 1_000_000_000

    return int(number * multiplier)


def normalize_hours_away(value: Any) -> int:
    try:
        hours = int(value or 0)
    except Exception:
        return 0
    if hours in ALLOWED_HOURS_AWAY:
        return hours
    if hours < 4:
        return 0
    rounded = int(round(hours / 4) * 4)
    if rounded in ALLOWED_HOURS_AWAY:
        return rounded
    return 48 if rounded > 48 else 0


def normalize_settings_input(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = DEFAULT_SETTINGS.copy()
    normalized.update(settings)

    normalized["budget"] = parse_budget_value(normalized.get("budget", 0))
    normalized["available_slots"] = int(normalized.get("available_slots", 1) or 1)
    normalized["hours_away"] = normalize_hours_away(normalized.get("hours_away", 0))
    normalized["openai_api_key"] = str(normalized.get("openai_api_key", "") or "").strip()
    normalized["openai_api_key_path"] = str(normalized.get("openai_api_key_path", "") or "").strip()

    model = str(normalized.get("openai_model", DEFAULT_SETTINGS["openai_model"]) or "").strip()
    normalized["openai_model"] = model if model in ALLOWED_MODELS else DEFAULT_SETTINGS["openai_model"]

    return normalized


def load_settings() -> dict[str, Any]:
    os.makedirs(SETTINGS_DIR, exist_ok=True)

    if not os.path.exists(SETTINGS_PATH):
        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return DEFAULT_SETTINGS.copy()

    return normalize_settings_input(data)


def merge_settings_update(settings: dict[str, Any], *, allow_secret_updates: bool = False) -> dict[str, Any]:
    current = load_settings()
    incoming = normalize_settings_input(settings)

    merged = {**current, **incoming}
    for field in SECRET_SETTING_FIELDS:
        if allow_secret_updates:
            if field in settings and str(settings.get(field, "") or "").strip():
                merged[field] = str(settings.get(field, "") or "").strip()
            else:
                merged[field] = current.get(field, "")
        else:
            merged[field] = current.get(field, "")

    return normalize_settings_input(merged)


def to_public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_settings_input(settings)
    public = {k: v for k, v in normalized.items() if k not in SECRET_SETTING_FIELDS}
    public["has_openai_api_key"] = bool(str(normalized.get("openai_api_key", "") or "").strip())
    public["has_openai_api_key_path"] = bool(str(normalized.get("openai_api_key_path", "") or "").strip())
    return public


def save_settings(settings: dict[str, Any], *, allow_secret_updates: bool = False) -> dict[str, Any]:
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    normalized = merge_settings_update(settings, allow_secret_updates=allow_secret_updates)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2)
    return normalized


def resolve_openai_api_key(settings: dict[str, Any]) -> str:
    inline_key = str(settings.get("openai_api_key", "") or "").strip()
    if inline_key:
        return inline_key

    key_path = str(settings.get("openai_api_key_path", "") or "").strip()
    if not key_path:
        return ""

    try:
        with open(key_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""
