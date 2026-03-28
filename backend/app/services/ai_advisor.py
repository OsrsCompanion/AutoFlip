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


def _default_question() -> str:
    return "Review my current trades and recommendations. Tell me what to keep, replace, or cancel."


def _extract_json_text(response) -> str:
    text = getattr(response, "output_text", "") or ""
    return text.strip() if text else ""


def _coerce_json_reply(raw_text: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {
        "summary": raw_text[:240] if raw_text else "No reply.",
        "actions": [],
        "top_picks": [],
        "notes": [],
    }


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
            if name and _normalize_name(name) in text:
                names.append(name)

    if user_message.strip():
        names.append(user_message.strip())

    return _unique_names(names)[:4]


def _format_historical_context_block(payload: dict[str, Any]) -> list[str]:
    if not payload or not payload.get("found"):
        return []

    current_market = payload.get("current_market", {}) or {}
    ranges = payload.get("ranges", {}) or {}
    signals = payload.get("signals", {}) or {}
    cache_metrics = payload.get("cache_metrics", {}) or {}
    current_offer = payload.get("current_offer")
    confidence = payload.get("confidence", {}) or {}
    answerability = payload.get("answerability", {}) or {}
    question_profile = payload.get("question_profile", {}) or {}

    lines: list[str] = []
    lines.append(f"Historical item context: {payload.get('item_name', 'Unknown')}")
    lines.append(
        f"- buy={current_market.get('buy_price', 0)}"
        f" | sell={current_market.get('sell_price', 0)}"
        f" | spread={current_market.get('spread', 0)}"
        f" | roi_pct={current_market.get('roi_pct', 0)}"
        f" | recent_volume_1h={current_market.get('recent_volume_1h', 0)}"
        f" | avg_daily_volume={current_market.get('avg_daily_volume', 0)}"
    )
    lines.append(
        f"- last_4h_trend={signals.get('last_4h_trend', 'unknown')} ({signals.get('last_4h_trend_pct', 0)}%)"
        f" | day_trend={signals.get('day_trend', 'unknown')} ({signals.get('day_trend_pct', 0)}%)"
        f" | week_trend={signals.get('week_trend', 'unknown')} ({signals.get('week_trend_pct', 0)}%)"
        f" | month_trend={signals.get('month_trend', 'unknown')} ({signals.get('month_trend_pct', 0)}%)"
    )
    lines.append(
        f"- day_range={ranges.get('day_low', 0)}-{ranges.get('day_high', 0)}"
        f" | week_range={ranges.get('week_low', 0)}-{ranges.get('week_high', 0)}"
        f" | month_range={ranges.get('month_low', 0)}-{ranges.get('month_high', 0)}"
    )
    lines.append(
        f"- crash_risk={signals.get('crash_risk', 'unknown')}"
        f" | entry_signal={signals.get('entry_signal', 'unknown')}"
        f" | week_position_pct={ranges.get('week_position_pct', 0)}"
        f" | month_position_pct={ranges.get('month_position_pct', 0)}"
    )
    lines.append(
        f"- requested_horizon={question_profile.get('requested_horizon', 'general')}"
        f" | preferred_window={question_profile.get('preferred_window', 'day')}"
        f" | answerability_verdict={answerability.get('answerability_verdict', 'unknown')}"
        f" | answerability_summary={answerability.get('answerability_summary', '')}"
    )
    for key in ("last_4h", "day", "week", "month"):
        window = confidence.get(key, {}) or {}
        lines.append(
            f"- confidence_{key}={window.get('confidence_level', 'unknown')}"
            f" | points_{key}={window.get('points', 0)}"
            f" | confidence_summary_{key}={window.get('confidence_summary', '')}"
        )
    lines.append(
        f"- dip_vs_day_pct={cache_metrics.get('dip_vs_day_pct', 0)}"
        f" | dip_vs_week_pct={cache_metrics.get('dip_vs_week_pct', 0)}"
        f" | dip_vs_month_pct={cache_metrics.get('dip_vs_month_pct', 0)}"
        f" | stability_week_pct={cache_metrics.get('stability_week_pct', 0)}"
        f" | updated_at={cache_metrics.get('updated_at', '-')}"
    )
    lines.append(f"- advisor_hint={signals.get('advisor_hint', '')}")
    if current_offer:
        lines.append(
            f"- current_offer_state={current_offer.get('state', '-')}"
            f" | current_offer_price={current_offer.get('coin_amount', '-')}"
            f" | current_offer_qty={current_offer.get('quantity_text', '-')}"
        )
    return lines


def _build_context(
    settings: dict[str, Any],
    current_scan: dict[str, Any],
    decisions: dict[str, Any],
    recommendations: dict[str, Any],
    user_message: str,
) -> str:
    lines: list[str] = []
    lines.append("OSRS Flip Assistant context")
    lines.append(f"Budget: {settings.get('budget', 0)} gp")
    lines.append(f"Available slots: {settings.get('available_slots', 0)}")
    lines.append(f"Hours away: {settings.get('hours_away', 0)}")
    lines.append("")

    lines.append("Current offers:")
    offers = current_scan.get("offers", [])
    if not offers:
        lines.append("- none detected")
    else:
        for offer in offers:
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
        for decision in decision_rows[:8]:
            lines.append(
                f"- {decision.get('item_name', 'Unknown')} | action={decision.get('decision', '-')}"
                f" | listed_price={decision.get('listed_price', '-')}"
                f" | market_buy={decision.get('market_buy_price', '-')}"
                f" | market_sell={decision.get('market_sell_price', '-')}"
                f" | profit_per_item={decision.get('profit_per_item', '-')}"
                f" | note={decision.get('summary', '')}"
            )
    lines.append("")

    lines.append("Top recommendations:")
    recommendation_rows: list[dict[str, Any]] = []
    for bucket_name in ("recommendations", "high_value", "overnight", "anchors", "dump"):
        recommendation_rows.extend(recommendations.get(bucket_name, []) or [])
    if not recommendation_rows:
        lines.append("- none")
    else:
        for item in recommendation_rows[:8]:
            lines.append(
                f"- {item.get('name', 'Unknown')} | buy={item.get('buy_price', '-')}"
                f" | sell={item.get('sell_price', '-')}"
                f" | profit_per_item={item.get('profit_per_item', '-')}"
                f" | quantity={item.get('suggested_quantity', '-')}"
                f" | potential_profit={item.get('potential_profit', '-')}"
            )
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

    if not added_context and offers:
        first_offer_name = str(offers[0].get("item_name") or "").strip()
        if first_offer_name:
            payload = build_ai_context_for_query(first_offer_name, current_scan=current_scan)
            if payload.get("found"):
                lines.extend(_format_historical_context_block(payload))
                lines.append("")
                added_context = True

    if not added_context:
        lines.append("- no matching cached historical item context found")
        lines.append("")

    return "\n".join(lines)


def build_ai_advice(user_message: str) -> dict:
    settings = load_settings()
    api_key = resolve_openai_api_key(settings)
    if not api_key:
        return {
            "error": "No OpenAI API key found. Add a key or import a .txt key file in Personal Settings first.",
            "reply_json": None,
        }

    current_scan = get_current_offers()
    decisions = build_trade_decisions(settings=settings, current_scan=current_scan)
    recommendations = build_recommendations(settings=settings, current_scan=current_scan)
    context = _build_context(settings, current_scan, decisions, recommendations, user_message=user_message)
    prompt = user_message.strip() or _default_question()
    model = settings.get("openai_model", DEFAULT_MODEL) or DEFAULT_MODEL

    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=model,
            max_output_tokens=300,
            input=[
                {
                    "role": "developer",
                    "content": (
                        "You are an OSRS flipping advisor. "
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
                        "Grand Exchange tax is already included in backend profit values. "
                        "You must respect backend answerability and confidence. "
                        "If preferred-window confidence is very_low or low, avoid strong multi-day claims. "
                        "If requested_horizon is longer_term and answerability says insufficient or limited, say data is thin. "
                        "If requested_horizon is short_term and last_4h or day confidence is medium/high, you may give a more confident short-term answer. "
                        "When data is thin, explicitly say the answer is low-confidence rather than pretending certainty. "
                        "Prefer backend historical signals over generic advice. "
                        "Keep summary under 26 words. "
                        "Keep each reason under 18 words. "
                        "Use at most 4 actions, 3 top_picks, 3 notes."
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
            "reply_json": None,
            "model": model,
        }

    raw_text = _extract_json_text(response)
    reply_json = _coerce_json_reply(raw_text)

    return {
        "model": model,
        "reply_json": reply_json,
        "raw_reply": raw_text,
        "current_scan": current_scan,
        "decisions": decisions,
        "recommendations": recommendations,
    }
