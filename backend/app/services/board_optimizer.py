from __future__ import annotations

import re
from typing import Any

ITEM_ICON_BASE_URL = "/static/item_icons"
ITEM_ICON_PLACEHOLDER_URL = f"{ITEM_ICON_BASE_URL}/placeholder.png"

from app.services.item_metadata import get_buy_limit, load_item_metadata_cache
from app.services.recommendation_candidate_cache import load_recommendation_candidate_cache

DEFAULT_SLOTS = 8
MAX_SLOTS = 8
DEFAULT_RISK_MODE = "balanced"
DEFAULT_MEMBERSHIP_MODE = "members"

RISK_MODES = {
    "safe": {
        "min_confidence": {"high", "medium"},
        "allowed_speed": {"fast", "medium"},
        "spike_penalty": 0.55,
        "weak_penalty": 0.35,
        "slow_penalty": 0.45,
        "concentration_cap": 0.22,
        "category_budget_cap": 0.35,
        "max_category_slots": 3,
        "max_buy_limit_cycles": 2.0,
        "capital_fit_power": 0.55,
    },
    "balanced": {
        "min_confidence": {"high", "medium", "weak"},
        "allowed_speed": {"fast", "medium", "slow", "thin"},
        "spike_penalty": 0.75,
        "weak_penalty": 0.65,
        "slow_penalty": 0.75,
        "concentration_cap": 0.30,
        "category_budget_cap": 0.42,
        "max_category_slots": 3,
        "max_buy_limit_cycles": 3.0,
        "capital_fit_power": 0.45,
    },
    "aggressive": {
        "min_confidence": {"high", "medium", "weak"},
        "allowed_speed": {"fast", "medium", "slow", "thin"},
        "spike_penalty": 0.90,
        "weak_penalty": 0.85,
        "slow_penalty": 0.95,
        "concentration_cap": 0.38,
        "category_budget_cap": 0.55,
        "max_category_slots": 4,
        "max_buy_limit_cycles": 4.0,
        "capital_fit_power": 0.35,
    },
}

SPEED_HOURS = {
    "fast": 1.0,
    "medium": 3.0,
    "slow": 8.0,
    "thin": 12.0,
}

CONFIDENCE_FACTOR = {
    "high": 1.00,
    "medium": 0.82,
    "weak": 0.55,
}

SPEED_FACTOR = {
    "fast": 1.00,
    "medium": 0.82,
    "slow": 0.58,
    "thin": 0.42,
}


CATEGORY_KEYWORDS = {
    "runes_ammo": (" rune", "rune ", "bolt", "arrow", "dart", "cannonball", "scale"),
    "potions_food": ("potion", "brew", "restore", "shark", "anglerfish", "karambwan", "manta ray", "food"),
    "herbs_farming": ("seed", "grimy", "herb", "sapling", "compost", "snape grass"),
    "logs_planks": ("log", "plank", "kindling"),
    "ores_bars": ("ore", "bar", "coal", "runite", "adamantite", "mithril"),
    "weapons_armor": ("sword", "scimitar", "whip", "bow", "staff", "helm", "body", "legs", "shield", "plate", "crossbow"),
    "jewellery": ("ring", "amulet", "necklace", "bracelet", "enchanted"),
    "materials": ("hide", "leather", "cloth", "thread", "nail", "kit", "repair"),
}


def _category_for_candidate(candidate: dict[str, Any]) -> str:
    explicit = candidate.get("category") or candidate.get("item_category") or candidate.get("category_tag")
    if explicit:
        return str(explicit).strip().lower().replace(" ", "_") or "other"
    tags = candidate.get("tags")
    if isinstance(tags, list) and tags:
        return str(tags[0]).strip().lower().replace(" ", "_") or "other"
    name = str(candidate.get("item_name") or candidate.get("name") or "").lower()
    for category, needles in CATEGORY_KEYWORDS.items():
        if any(needle in name for needle in needles):
            return category
    return "other"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def parse_budget(value: Any) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").strip().lower().replace(",", "")
    if not text:
        return 0
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", text)
    if not match:
        return max(0, _to_int(value, 0))
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return max(0, int(number * multiplier))


def _candidate_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("candidates") or payload.get("items") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _normalize_risk_mode(value: Any) -> str:
    text = str(value or DEFAULT_RISK_MODE).strip().lower().replace("_", "-")
    if text in {"safe", "safe-turnover", "low", "conservative"}:
        return "safe"
    if text in {"aggressive", "high"}:
        return "aggressive"
    return "balanced"


def _normalize_membership_mode(value: Any) -> str:
    text = str(value or DEFAULT_MEMBERSHIP_MODE).strip().lower().replace("_", "-")
    if text in {"f2p", "free", "free-to-play", "freeplay", "non-members", "nonmembers"}:
        return "f2p"
    return "members"


def _item_is_members_only(item_id: int, metadata: dict[str, dict[str, Any]]) -> bool:
    row = metadata.get(str(item_id), {}) if item_id > 0 else {}
    return bool(row.get("members"))


def _membership_allowed(candidate: dict[str, Any], *, membership_mode: str, metadata: dict[str, dict[str, Any]]) -> bool:
    if membership_mode != "f2p":
        return True
    item_id = _to_int(candidate.get("item_id") or candidate.get("id"))
    return not _item_is_members_only(item_id, metadata)


def _horizon_factor(fill_speed: str, expected_hours: float, hours_away: float) -> float:
    if hours_away <= 0:
        return 1.0
    expected = expected_hours or SPEED_HOURS.get(fill_speed, 6.0)
    if expected <= max(hours_away, 0.25):
        return 1.0
    ratio = hours_away / expected
    return max(0.25, min(1.0, ratio))


def _buy_limit_cycles(hours_away: float, risk_mode: str) -> float:
    risk = RISK_MODES[risk_mode]
    raw_cycles = max(1.0, hours_away / 4.0) if hours_away > 0 else 1.0
    return min(raw_cycles, float(risk.get("max_buy_limit_cycles", 3.0)))


def _candidate_buy_limit(candidate: dict[str, Any], metadata: dict[str, dict[str, Any]]) -> int:
    item_id = _to_int(candidate.get("item_id") or candidate.get("id"))
    direct = _to_int(candidate.get("buy_limit") or candidate.get("limit"))
    return direct if direct > 0 else get_buy_limit(item_id, metadata)


def _time_adjusted_buy_limit_capacity(candidate: dict[str, Any], *, hours_away: float, risk_mode: str, metadata: dict[str, dict[str, Any]]) -> int:
    buy_limit = _candidate_buy_limit(candidate, metadata)
    if buy_limit <= 0:
        return 0
    return max(1, int(buy_limit * _buy_limit_cycles(hours_away, risk_mode)))


def _liquidity_safe_quantity(candidate: dict[str, Any]) -> int:
    # Canonical field first. Legacy aliases remain for old caches/routes during migration.
    return _to_int(
        candidate.get("liquidity_safe_quantity")
        or candidate.get("suggested_quantity")
        or candidate.get("max_quantity")
    )


def _realistic_quantity_cap(candidate: dict[str, Any], *, hours_away: float, risk_mode: str, metadata: dict[str, dict[str, Any]]) -> int:
    liquidity_cap = _liquidity_safe_quantity(candidate)
    buy_limit_cap = _time_adjusted_buy_limit_capacity(candidate, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)

    caps = [cap for cap in (liquidity_cap, buy_limit_cap) if cap > 0]
    return min(caps) if caps else liquidity_cap


def _capital_fit_factor(candidate: dict[str, Any], *, budget: int, hours_away: float, risk_mode: str, metadata: dict[str, dict[str, Any]]) -> float:
    if budget <= 0:
        return 0.0

    buy_price = _to_int(candidate.get("buy_price"))
    if buy_price <= 0:
        return 0.0

    quantity_cap = _realistic_quantity_cap(candidate, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)
    max_deployable_gp = _to_int(candidate.get("max_deployable_gp"))
    realistic_gp = quantity_cap * buy_price if quantity_cap > 0 else 0

    if max_deployable_gp > 0:
        realistic_gp = min(realistic_gp, max_deployable_gp) if realistic_gp > 0 else max_deployable_gp

    if realistic_gp <= 0:
        return 0.15

    ratio = max(0.0, min(1.0, realistic_gp / max(1, budget)))
    power = float(RISK_MODES[risk_mode].get("capital_fit_power", 0.45))
    return max(0.12, min(1.0, ratio ** power))


def _planner_score(
    candidate: dict[str, Any],
    *,
    budget: int,
    hours_away: float,
    risk_mode: str,
    ml_weight: float,
    metadata: dict[str, dict[str, Any]],
) -> float:
    risk = RISK_MODES[risk_mode]
    expected_profit = _to_float(candidate.get("expected_profit"))
    expected_hours = _to_float(candidate.get("expected_time_to_liquidity_hours"), 1.0) or 1.0
    profit_per_hour = _to_float(candidate.get("profit_per_hour")) or (expected_profit / expected_hours if expected_hours else expected_profit)
    fill_probability = _to_float(candidate.get("market_proxy_fill_probability") or candidate.get("fill_probability"), 0.5)
    stability_factor = _to_float(candidate.get("stability_factor"), 0.7)
    if stability_factor > 1.0:
        stability_factor = stability_factor / 100.0
    confidence = str(candidate.get("confidence_band") or "weak").lower()
    speed = str(candidate.get("fill_speed_band") or "slow").lower()

    capital_fit = _capital_fit_factor(candidate, budget=budget, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)
    horizon = _horizon_factor(speed, expected_hours, hours_away)

    score = profit_per_hour * fill_probability * max(stability_factor, 0.05) * capital_fit * horizon
    score *= CONFIDENCE_FACTOR.get(confidence, 0.5)
    score *= SPEED_FACTOR.get(speed, 0.5)

    risk_evidence = candidate.get("risk_evidence") if isinstance(candidate.get("risk_evidence"), dict) else {}
    if risk_evidence.get("too_spiky"):
        score *= risk["spike_penalty"]
    if confidence == "weak":
        score *= risk["weak_penalty"]
    if speed in {"slow", "thin"}:
        score *= risk["slow_penalty"]

    ml_score = candidate.get("ml_score")
    if ml_score is not None and ml_weight > 0:
        ml_component = _to_float(ml_score)
        deterministic_weight = max(0.0, 1.0 - ml_weight)
        score = (score * deterministic_weight) + (ml_component * ml_weight)
    return round(score, 6)


def _icon_url_for_item_id(item_id: int) -> str:
    return f"{ITEM_ICON_BASE_URL}/{item_id}.png" if item_id > 0 else ITEM_ICON_PLACEHOLDER_URL


def _slot_from_candidate(
    candidate: dict[str, Any],
    slot_number: int,
    quantity: int,
    score: float,
    *,
    hours_away: float,
    risk_mode: str,
    metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    buy_price = _to_int(candidate.get("buy_price"))
    sell_price = _to_int(candidate.get("sell_price"))
    profit_per_item = _to_int(candidate.get("profit_after_tax_per_item"))
    expected_profit = quantity * profit_per_item
    item_id = _to_int(candidate.get("item_id") or candidate.get("id"))
    buy_limit = _candidate_buy_limit(candidate, metadata)
    buy_limit_cycles = _buy_limit_cycles(hours_away, risk_mode)
    time_adjusted_buy_limit_quantity = _time_adjusted_buy_limit_capacity(candidate, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)
    liquidity_safe_quantity = _liquidity_safe_quantity(candidate)
    return {
        "slot": slot_number,
        "action": "buy",
        "plan_type": candidate.get("plan_type") or "flip",
        "item_id": item_id,
        "item_name": str(candidate.get("item_name") or candidate.get("name") or "Unknown item"),
        "icon_url": candidate.get("icon_url") or _icon_url_for_item_id(item_id),
        "quantity": quantity,
        "allocated_quantity": quantity,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "capital_required": quantity * buy_price,
        "profit_after_tax_per_item": profit_per_item,
        "expected_profit": expected_profit,
        "roi_pct_after_tax": _to_float(candidate.get("roi_pct_after_tax")),
        "expected_time_to_liquidity_hours": _to_float(candidate.get("expected_time_to_liquidity_hours")),
        "profit_per_hour": _to_float(candidate.get("profit_per_hour")),
        "fill_probability": _to_float(candidate.get("market_proxy_fill_probability") or candidate.get("fill_probability")),
        "stability_factor": _to_float(candidate.get("stability_factor")),
        "confidence_band": candidate.get("confidence_band"),
        "fill_speed_band": candidate.get("fill_speed_band"),
        "category": _category_for_candidate(candidate),
        "members": _item_is_members_only(item_id, metadata),
        "buy_limit_4h": buy_limit,
        "buy_limit_cycles": round(buy_limit_cycles, 3),
        "time_adjusted_buy_limit_quantity": time_adjusted_buy_limit_quantity,
        "liquidity_safe_quantity": liquidity_safe_quantity,
        "score": score,
        "ml_score": candidate.get("ml_score"),
        "ml_weight_applied": 0.0,
        "short_reason": candidate.get("short_reason") or "good fit for this board",
    }


def _speed_mix(board: list[dict[str, Any]]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for slot in board:
        speed = str(slot.get("fill_speed_band") or "unknown").strip().lower() or "unknown"
        mix[speed] = mix.get(speed, 0) + 1
    return {key: mix[key] for key in sorted(mix)}


def _confidence_mix(board: list[dict[str, Any]]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for slot in board:
        confidence = str(slot.get("confidence_band") or "unknown").strip().lower() or "unknown"
        mix[confidence] = mix.get(confidence, 0) + 1
    return {key: mix[key] for key in sorted(mix)}


def _openai_slot_fact(slot: dict[str, Any]) -> dict[str, Any]:
    scoring = slot.get("scoring_metadata") if isinstance(slot.get("scoring_metadata"), dict) else {}
    return {
        "slot": _to_int(slot.get("slot")),
        "action": slot.get("action") or "buy",
        "plan_type": slot.get("plan_type") or "flip",
        "item_id": _to_int(slot.get("item_id")),
        "item_name": slot.get("item_name") or "Unknown item",
        "quantity": _to_int(slot.get("allocated_quantity") or slot.get("quantity")),
        "buy_price": _to_int(slot.get("buy_price")),
        "sell_price": _to_int(slot.get("sell_price")),
        "capital_required": _to_int(slot.get("capital_required")),
        "expected_profit": _to_int(slot.get("expected_profit")),
        "profit_after_tax_per_item": _to_int(slot.get("profit_after_tax_per_item")),
        "roi_pct_after_tax": _to_float(slot.get("roi_pct_after_tax")),
        "confidence_band": slot.get("confidence_band") or "unknown",
        "fill_speed_band": slot.get("fill_speed_band") or "unknown",
        "fill_probability": _to_float(slot.get("fill_probability")),
        "stability_factor": _to_float(slot.get("stability_factor")),
        "expected_time_to_liquidity_hours": _to_float(slot.get("expected_time_to_liquidity_hours")),
        "category": slot.get("category") or "other",
        "members": bool(slot.get("members")),
        "buy_limit_4h": _to_int(slot.get("buy_limit_4h")),
        "buy_limit_cycles": _to_float(slot.get("buy_limit_cycles")),
        "time_adjusted_buy_limit_quantity": _to_int(slot.get("time_adjusted_buy_limit_quantity")),
        "liquidity_safe_quantity": _to_int(slot.get("liquidity_safe_quantity")),
        "score": _to_float(slot.get("score")),
        "short_reason": slot.get("short_reason") or "good fit for this board",
        "facts": {
            "uses_liquidity_safe_quantity": _to_int(slot.get("liquidity_safe_quantity")) > 0,
            "realistic_quantity_cap": _to_int(scoring.get("realistic_quantity_cap")),
            "capital_fit_factor": _to_float(scoring.get("capital_fit_factor")),
            "quantity_schema": scoring.get("quantity_schema") or "liquidity_safe_quantity_v1",
            "buy_limit_source": scoring.get("buy_limit_source") or "unknown",
        },
    }


def _build_openai_recommendation_report(
    *,
    board: list[dict[str, Any]],
    budget: int,
    spent: int,
    slots_requested: int,
    risk_mode: str,
    membership_mode: str,
    hours_away: float,
    candidate_count: int,
    eligible_candidate_count: int,
) -> dict[str, Any]:
    slot_facts = [_openai_slot_fact(slot) for slot in board]
    expected_profit = sum(_to_int(slot.get("expected_profit")) for slot in board)
    board_score = round(sum(_to_float(slot.get("score")) for slot in board), 6)
    return {
        "schema": "openai_recommendation_report_v1",
        "purpose": "facts_only_user_facing_recommendation_summary",
        "generation_rules": {
            "backend_decides_recommendations": True,
            "openai_should_not_change_items_prices_or_quantities": True,
            "tone": "brief, natural, helpful, beginner-friendly",
            "avoid": [
                "guaranteed profit claims",
                "changing the plan",
                "inventing missing reasons",
                "complex finance jargon",
            ],
        },
        "user_context": {
            "budget": budget,
            "slots_requested": slots_requested,
            "slots_filled": len(board),
            "hours_away": hours_away,
            "risk_mode": risk_mode,
            "membership_mode": membership_mode,
        },
        "board_summary": {
            "plan_type": "eight_slot_board",
            "balance_profile": "best_balance",
            "budget": budget,
            "spent_gp": spent,
            "unallocated_gp": max(0, budget - spent),
            "expected_profit": expected_profit,
            "board_score": board_score,
            "candidate_count": candidate_count,
            "eligible_candidate_count": eligible_candidate_count,
            "speed_mix": _speed_mix(board),
            "confidence_mix": _confidence_mix(board),
            "main_reason": "This plan spreads GP across realistic flips that fit the budget, time window, liquidity, buy limits, and risk setting.",
        },
        "slots": slot_facts,
        "recommended_response_contract": {
            "max_sentences": 4,
            "must_include": ["overall plan", "best practical next action", "simple reason"],
            "may_include": ["one or two standout items", "confidence/fill speed summary"],
        },
    }


def build_board_plan(settings: dict[str, Any] | None = None, candidate_cache: Any | None = None) -> dict[str, Any]:
    settings = settings or {}
    budget = parse_budget(settings.get("budget") or settings.get("available_budget") or settings.get("liquid_gp"))
    slots = max(1, min(MAX_SLOTS, _to_int(settings.get("slots") or settings.get("available_slots") or settings.get("slots_available"), DEFAULT_SLOTS)))
    hours_away = max(0.0, _to_float(settings.get("hours_away") or settings.get("horizon_hours"), 1.0))
    risk_mode = _normalize_risk_mode(settings.get("risk_mode") or settings.get("risk") or settings.get("preset"))
    membership_mode = _normalize_membership_mode(settings.get("membership_mode") or settings.get("membership") or settings.get("account_type") or settings.get("world_type"))
    ml_weight = max(0.0, min(1.0, _to_float(settings.get("ml_weight"), 0.0)))
    metadata = load_item_metadata_cache()

    if candidate_cache is None:
        candidate_cache = load_recommendation_candidate_cache()
    raw_candidates = _candidate_items(candidate_cache)
    if budget <= 0:
        return {
            "status": "error",
            "reason": "budget_required",
            "message": "Budget must be greater than zero.",
            "slots_requested": slots,
            "budget": budget,
            "risk_mode": risk_mode,
            "membership_mode": membership_mode,
            "hours_away": hours_away,
            "ml_weight": ml_weight,
            "candidate_count": len(raw_candidates),
            "board": [],
        }
    risk = RISK_MODES[risk_mode]
    concentration_cap = max(1, int(budget * risk["concentration_cap"]))

    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in raw_candidates:
        buy_price = _to_int(candidate.get("buy_price"))
        sell_price = _to_int(candidate.get("sell_price"))
        profit_per_item = _to_int(candidate.get("profit_after_tax_per_item"))
        confidence = str(candidate.get("confidence_band") or "weak").lower()
        speed = str(candidate.get("fill_speed_band") or "slow").lower()
        if buy_price <= 0 or sell_price <= 0 or profit_per_item <= 0:
            continue
        if buy_price > budget:
            continue
        if not _membership_allowed(candidate, membership_mode=membership_mode, metadata=metadata):
            continue
        if confidence not in risk["min_confidence"] or speed not in risk["allowed_speed"]:
            continue
        score = _planner_score(
            candidate,
            budget=budget,
            hours_away=hours_away,
            risk_mode=risk_mode,
            ml_weight=ml_weight,
            metadata=metadata,
        )
        if score <= 0:
            continue
        scored.append((score, candidate))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    board: list[dict[str, Any]] = []
    used_item_ids: set[int] = set()
    category_slots: dict[str, int] = {}
    category_spend: dict[str, int] = {}
    spent = 0
    max_category_slots = int(risk.get("max_category_slots", slots))
    category_budget_cap = max(1, int(budget * float(risk.get("category_budget_cap", 1.0))))
    for score, candidate in scored:
        if len(board) >= slots:
            break
        item_id = _to_int(candidate.get("item_id") or candidate.get("id"))
        if item_id in used_item_ids:
            continue
        category = _category_for_candidate(candidate)
        if category_slots.get(category, 0) >= max_category_slots:
            continue
        buy_price = _to_int(candidate.get("buy_price"))
        realistic_qty_cap = _realistic_quantity_cap(candidate, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)
        max_deployable_gp = _to_int(candidate.get("max_deployable_gp"))
        remaining_budget = budget - spent
        if remaining_budget < buy_price:
            continue
        remaining_category_budget = category_budget_cap - category_spend.get(category, 0)
        if remaining_category_budget < buy_price:
            continue

        slot_cap = min(concentration_cap, remaining_budget, remaining_category_budget)
        if max_deployable_gp > 0:
            slot_cap = min(slot_cap, max_deployable_gp)

        quantity = slot_cap // buy_price
        if realistic_qty_cap > 0:
            quantity = min(quantity, realistic_qty_cap)
        if quantity <= 0:
            continue

        slot = _slot_from_candidate(candidate, len(board) + 1, quantity, score, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)
        slot["ml_weight_applied"] = ml_weight
        slot["category"] = category
        slot.setdefault("scoring_metadata", {})
        slot["scoring_metadata"]["category_slots_before"] = category_slots.get(category, 0)
        slot["scoring_metadata"]["category_budget_cap"] = category_budget_cap
        slot["scoring_metadata"]["max_category_slots"] = max_category_slots
        slot["scoring_metadata"]["capital_fit_factor"] = _capital_fit_factor(candidate, budget=budget, hours_away=hours_away, risk_mode=risk_mode, metadata=metadata)
        slot["scoring_metadata"]["realistic_quantity_cap"] = realistic_qty_cap
        slot["scoring_metadata"]["buy_limit_source"] = "item_metadata" if slot["buy_limit_4h"] > 0 else "missing"
        slot["scoring_metadata"]["quantity_schema"] = "liquidity_safe_quantity_v1"
        board.append(slot)
        used_item_ids.add(item_id)
        category_slots[category] = category_slots.get(category, 0) + 1
        category_spend[category] = category_spend.get(category, 0) + slot["capital_required"]
        spent += slot["capital_required"]
    openai_recommendation_report = _build_openai_recommendation_report(
        board=board,
        budget=budget,
        spent=spent,
        slots_requested=slots,
        risk_mode=risk_mode,
        membership_mode=membership_mode,
        hours_away=hours_away,
        candidate_count=len(raw_candidates),
        eligible_candidate_count=len(scored),
    )

    return {
        "status": "ok" if board else "empty",
        "plan_type": "eight_slot_board",
        "budget": budget,
        "slots_requested": slots,
        "slots_filled": len(board),
        "risk_mode": risk_mode,
        "membership_mode": membership_mode,
        "hours_away": hours_away,
        "buy_limit_cycles": round(_buy_limit_cycles(hours_away, risk_mode), 3),
        "ml_weight": ml_weight,
        "deterministic_weight": round(1.0 - ml_weight, 3),
        "candidate_count": len(raw_candidates),
        "eligible_candidate_count": len(scored),
        "item_metadata_count": len(metadata),
        "membership_filter": {
            "mode": membership_mode,
            "members_items_allowed": membership_mode != "f2p",
        },
        "spent_gp": spent,
        "category_summary": {
            category: {"slots": category_slots.get(category, 0), "capital_required": category_spend.get(category, 0)}
            for category in sorted(category_slots)
        },
        "diversification_rules": {
            "max_category_slots": max_category_slots,
            "category_budget_cap": category_budget_cap,
        },
        "unallocated_gp": max(0, budget - spent),
        "expected_profit": sum(_to_int(slot.get("expected_profit")) for slot in board),
        "board_score": round(sum(_to_float(slot.get("score")) for slot in board), 6),
        "board": board,
        "openai_recommendation_report": openai_recommendation_report,
        "recommendation_report": openai_recommendation_report,
        "explanation": {
            "what_to_do": "Place the listed buy offers.",
            "why": "The board uses profitable candidates that fit your budget, risk setting, liquidity, buy limits, and time window.",
            "what_you_gain": "Your GP is spread across realistic flips instead of being ranked only by ROI.",
        },
    }
