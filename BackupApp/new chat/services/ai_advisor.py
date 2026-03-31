from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.services.ai_context import build_ai_context_for_query
from app.services.recommendations import build_recommendations
from app.services.screen_snapshot import get_current_offers
from app.services.settings_store import load_settings, resolve_openai_api_key
from app.services.trade_decisions import build_trade_decisions

DEFAULT_MODEL = "gpt-5.4-mini"
MAX_ACTIONS = 3
MAX_PICKS = 3


def _default_question() -> str:
    return "Which items look safest to review on the website right now?"


def _extract_json_text(response) -> str:
    text = getattr(response, "output_text", "") or ""
    return text.strip() if text else ""


def _normalize_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _unique_names(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _candidate_item_names(user_message: str, current_scan: dict[str, Any], recommendations: dict[str, Any]) -> list[str]:
    text = _normalize_name(user_message)
    names: list[str] = []

    for offer in current_scan.get("offers", []) or []:
        name = str(offer.get("item_name") or "").strip()
        if name and (_normalize_name(name) in text or not text):
            names.append(name)

    for bucket_name in ("recommendations", "high_value", "overnight", "anchors", "dump"):
        for item in recommendations.get(bucket_name, []) or []:
            name = str(item.get("name") or "").strip()
            if name and (_normalize_name(name) in text or not text):
                names.append(name)

    if user_message.strip():
        names.append(user_message.strip())

    return _unique_names(names)[:3]


def _safe_decision(raw: Any) -> str:
    value = str(raw or "review").strip().lower()
    allowed = {"consider", "watch", "review", "hold", "avoid"}
    return value if value in allowed else "review"


def _safe_reason(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Useful to review, but full execution stays in the plugin."
    return text[:120]


def _sanitize_reply(reply_json: dict[str, Any], fallback_picks: list[str]) -> dict[str, Any]:
    summary = str(reply_json.get("summary") or "Good items to review, but the plugin handles the full plan.").strip()[:140]
    actions: list[dict[str, str]] = []
    for entry in reply_json.get("actions", []) or []:
        if not isinstance(entry, dict):
            continue
        item = str(entry.get("item") or "").strip()
        if not item:
            continue
        actions.append(
            {
                "item": item[:60],
                "decision": _safe_decision(entry.get("decision")),
                "reason": _safe_reason(entry.get("reason")),
            }
        )
        if len(actions) >= MAX_ACTIONS:
            break

    top_picks = []
    for value in reply_json.get("top_picks", []) or []:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in top_picks:
            top_picks.append(cleaned[:60])
        if len(top_picks) >= MAX_PICKS:
            break

    if not top_picks:
        top_picks = fallback_picks[:MAX_PICKS]

    notes = []
    for value in reply_json.get("notes", []) or []:
        cleaned = str(value or "").strip()
        if cleaned:
            notes.append(cleaned[:140])
        if len(notes) >= 2:
            break

    notes.append("Plugin unlock: full 8-slot optimization, quantities, and live execution guidance.")

    return {
        "summary": summary,
        "actions": actions,
        "top_picks": top_picks[:MAX_PICKS],
        "notes": notes[:3],
        "mode": "web_safe",
        "plugin_cta": "Use the RuneLite plugin for full slot-by-slot execution.",
    }


def _coerce_json_reply(raw_text: str, fallback_picks: list[str]) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            return _sanitize_reply(data, fallback_picks)
    except Exception:
        pass

    return _sanitize_reply(
        {
            "summary": raw_text[:140] if raw_text else "Good items to review, but the plugin handles the full plan.",
            "actions": [],
            "top_picks": fallback_picks,
            "notes": [],
        },
        fallback_picks,
    )


def _format_historical_context_block(payload: dict[str, Any]) -> list[str]:
    if not payload or not payload.get("found"):
        return []

    current_market = payload.get("current_market", {}) or {}
    signals = payload.get("signals", {}) or {}
    ranges = payload.get("ranges", {}) or {}

    return [
        f"Historical item context: {payload.get('item_name', 'Unknown')}",
        (
            f"- buy={current_market.get('buy_price', 0)}"
            f" | sell={current_market.get('sell_price', 0)}"
            f" | profit_per_item={current_market.get('profit_per_item', 0)}"
            f" | roi_pct={current_market.get('roi_pct', 0)}"
            f" | recent_volume_1h={current_market.get('recent_volume_1h', 0)}"
        ),
        (
            f"- day_trend={signals.get('day_trend', 'unknown')} ({signals.get('day_trend_pct', 0)}%)"
            f" | week_trend={signals.get('week_trend', 'unknown')} ({signals.get('week_trend_pct', 0)}%)"
            f" | crash_risk={signals.get('crash_risk', 'unknown')}"
            f" | entry_signal={signals.get('entry_signal', 'unknown')}"
        ),
        (
            f"- day_range={ranges.get('day_low', 0)}-{ranges.get('day_high', 0)}"
            f" | week_range={ranges.get('week_low', 0)}-{ranges.get('week_high', 0)}"
            f" | month_range={ranges.get('month_low', 0)}-{ranges.get('month_high', 0)}"
        ),
    ]


def _build_context(
    settings: dict[str, Any],
    current_scan: dict[str, Any],
    decisions: dict[str, Any],
    recommendations: dict[str, Any],
    user_message: str,
) -> tuple[str, list[str]]:
    lines: list[str] = []
    fallback_picks: list[str] = []

    lines.append("OSRS Flip Assistant website-safe context")
    lines.append(f"Budget: {settings.get('budget', 0)} gp")
    lines.append(f"Available slots: {settings.get('available_slots', 0)}")
    lines.append(f"Hours away: {settings.get('hours_away', 0)}")
    lines.append("")

    lines.append("Current offers:")
    offers = current_scan.get("offers", [])
    if not offers:
        lines.append("- none detected")
    else:
        for offer in offers[:4]:
            lines.append(
                f"- {offer.get('item_name', 'Unknown')} | state={offer.get('state', '-')}"
                f" | listed_price={offer.get('coin_amount', '-')}"
                f" | quantity={offer.get('quantity_text', '-')}"
            )
    lines.append("")

    lines.append("Current trade decisions:")
    decision_rows = decisions.get("decisions", []) or []
    if not decision_rows:
        lines.append("- none")
    else:
        for decision in decision_rows[:4]:
            lines.append(
                f"- {decision.get('item_name', 'Unknown')} | action={decision.get('decision', '-')}"
                f" | profit_per_item={decision.get('profit_per_item', '-')}"
                f" | note={decision.get('summary', '')}"
            )
    lines.append("")

    lines.append("Top recommendations (website-safe):")
    recommendation_rows: list[dict[str, Any]] = []
    for bucket_name in ("recommendations", "high_value", "overnight", "anchors", "dump"):
        recommendation_rows.extend(recommendations.get(bucket_name, []) or [])
    unique_seen: set[str] = set()
    for item in recommendation_rows:
        name = str(item.get("name") or "").strip()
        if not name or _normalize_name(name) in unique_seen:
            continue
        unique_seen.add(_normalize_name(name))
        fallback_picks.append(name)
        lines.append(
            f"- {name} | buy={item.get('buy_price', '-')}"
            f" | sell={item.get('sell_price', '-')}"
            f" | profit_per_item={item.get('profit_per_item', '-')}"
            f" | roi_pct={item.get('roi_pct', '-')}"
            f" | recent_volume={item.get('recent_volume', '-')}"
        )
        if len(fallback_picks) >= 3:
            break
    if not fallback_picks:
        lines.append("- none")
    lines.append("")

    lines.append("Historical market context:")
    candidate_names = _candidate_item_names(user_message, current_scan, recommendations)
    added_context = False
    for candidate in candidate_names:
        payload = build_ai_context_for_query(candidate, current_scan=current_scan)
        if payload.get("found"):
            lines.extend(_format_historical_context_block(payload))
            lines.append("")
            added_context = True
    if not added_context:
        lines.append("- no matching cached historical item context found")
        lines.append("")

    return "\n".join(lines), fallback_picks[:3]


def _fallback_reply(recommendations: dict[str, Any]) -> dict[str, Any]:
    picks = []
    actions = []
    for item in recommendations.get("recommendations", []) or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        picks.append(name)
        actions.append(
            {
                "item": name,
                "decision": "review",
                "reason": f"ROI {round(float(item.get('roi_pct', 0) or 0), 2)}% with volume {item.get('recent_volume', 0)}.",
            }
        )
        if len(picks) >= 3:
            break
    return _sanitize_reply(
        {
            "summary": "Good items are visible, but the plugin handles full execution.",
            "actions": actions,
            "top_picks": picks,
            "notes": ["Website AI explains ideas, not full plans."],
        },
        picks,
    )


def build_ai_advice(user_message: str) -> dict:
    settings = load_settings()
    current_scan = get_current_offers()
    decisions = build_trade_decisions(settings=settings, current_scan=current_scan)
    recommendations = build_recommendations(settings=settings, current_scan=current_scan, mode="web_safe")
    context, fallback_picks = _build_context(settings, current_scan, decisions, recommendations, user_message=user_message)
    prompt = user_message.strip() or _default_question()
    model = settings.get("openai_model", DEFAULT_MODEL) or DEFAULT_MODEL

    api_key = resolve_openai_api_key(settings)
    if not api_key:
        return {
            "model": "fallback-web-safe",
            "reply_json": _fallback_reply(recommendations),
            "raw_reply": "",
            "current_scan": current_scan,
            "decisions": decisions,
            "recommendations": recommendations,
        }

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            max_output_tokens=260,
            input=[
                {
                    "role": "developer",
                    "content": (
                        "You are an OSRS flipping advisor for the WEBSITE tier. "
                        "Reply with JSON only. No markdown, no prose outside JSON. "
                        "Use this exact schema: "
                        "{"
                        '"summary": string, '
                        '"actions": ['
                        "{"
                        '"item": string, '
                        '"decision": string, '
                        '"reason": string'
                        "}"
                        "], "
                        '"top_picks": [string], '
                        '"notes": [string]'
                        "}. "
                        "Allowed decisions: consider, watch, review, hold, avoid. "
                        "Never output a full GE plan. "
                        "Never assign all 8 slots. "
                        "Never give quantities, capital allocation, or sequential execution steps. "
                        "At most 3 actions, 3 top_picks, and 2 notes before the plugin reminder. "
                        "Keep summary under 24 words. "
                        "Keep each reason under 16 words. "
                        "Mention uncertainty when data is thin. "
                        "Frame the plugin as the place for full optimization and execution guidance."
                    ),
                },
                {
                    "role": "user",
                    "content": f"{context}\n\nUser request:\n{prompt}",
                },
            ],
        )
    except Exception as e:
        return {
            "error": f"AI request failed on model {model}: {str(e)}",
            "reply_json": _fallback_reply(recommendations),
            "model": model,
        }

    raw_text = _extract_json_text(response)
    reply_json = _coerce_json_reply(raw_text, fallback_picks)

    return {
        "model": model,
        "reply_json": reply_json,
        "raw_reply": raw_text,
        "current_scan": current_scan,
        "decisions": decisions,
        "recommendations": recommendations,
    }
