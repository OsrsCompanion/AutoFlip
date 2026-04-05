from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from openai import OpenAI

from app.services.ai_context import build_ai_context_for_query, load_market_cache
from app.services.recommendations import build_recommendations
from app.services.screen_snapshot import get_current_offers
from app.services.settings_store import load_settings, resolve_openai_api_key
from app.services.trade_decisions import build_trade_decisions

DEFAULT_MODEL = "gpt-5.4-mini"
MAX_ACTIONS = 3
MAX_PICKS = 3
AI_TIMEOUT_SECONDS = 10.5
FAST_PATH_PRESET_REPLIES = {
    "what should i replace if i will be away for 12 hours?": {
        "summary": "For a 12h window, swap out slower or thinner trades first.",
        "actions": [
            {"item": "Slowest-moving trades", "decision": "review", "reason": "Most likely to stall while you are offline."},
            {"item": "Thin-margin trades", "decision": "avoid", "reason": "Less room for drift over a longer hold."},
        ],
        "notes": [
            "Steadier, higher-volume items usually hold up better during offline windows.",
            "The plugin can narrow this into exact replacements once you upgrade.",
        ],
    },
    "which current trades look weakest and why?": {
        "summary": "Start with trades that combine slow movement and weak margin.",
        "actions": [
            {"item": "Slow-moving slots", "decision": "review", "reason": "Idle offers become dead weight fastest."},
            {"item": "Low-margin holds", "decision": "watch", "reason": "Small edge disappears first when prices drift."},
        ],
        "notes": [
            "Weakest usually means low velocity, low edge, or frequent repricing pressure.",
            "The plugin can rank every live slot once full tools are unlocked.",
        ],
    },
    "give me a preview-only 3-slot plan from my current setup.": {
        "summary": "Use a balanced 3-slot preview instead of forcing full deployment.",
        "actions": [
            {"item": "1 steadier slot", "decision": "consider", "reason": "Adds stability while you are away."},
            {"item": "1 medium-risk slot", "decision": "review", "reason": "Keeps upside without overcommitting budget."},
            {"item": "1 flexible slot", "decision": "hold", "reason": "Leaves room to rotate after fresh data."},
        ],
        "notes": [
            "A preview plan should spread risk rather than chase one item type.",
            "The plugin handles exact slot-by-slot assignment and quantities.",
        ],
    },
    "how should i lower risk without abandoning profit entirely?": {
        "summary": "Reduce reliance on volatile or slow items before cutting all upside.",
        "actions": [
            {"item": "High-volatility slots", "decision": "review", "reason": "These need more babysitting to stay efficient."},
            {"item": "Steadier volume items", "decision": "consider", "reason": "They usually carry lower stress over time."},
        ],
        "notes": [
            "Lower risk usually comes from smoother fills and more forgiving spreads.",
            "The plugin can convert this into an exact lower-risk allocation later.",
        ],
    },
}
PLANNING_KEYWORDS = (
    "best items",
    "all slots",
    "replace all",
    "full plan",
    "roadmap",
    "optimize all",
    "8 slots",
    "slot mix",
    "portfolio",
    "top trades",
    "full setup",
    "entire setup",
    "whole setup",
    "all my trades",
    "all my items",
    "rank these",
)
COMPARE_KEYWORDS = ("compare", "versus", "vs", "better than", "or")
NON_OSRS_BLOCKERS = (
    "math homework",
    "homework",
    "essay",
    "algebra",
    "geometry",
    "derivative",
)
STOPWORDS = {
    "a", "an", "and", "at", "be", "best", "buy", "can", "current", "flip", "for", "good", "how", "i", "if",
    "in", "is", "it", "my", "of", "on", "or", "price", "right", "sell", "should", "the", "to", "what", "which",
    "will", "with", "now", "much", "many", "hours", "hour", "away", "am", "me", "you", "your", "want", "save",
    "money", "period", "over", "during", "within", "from", "by", "please", "today", "tonight", "really", "exactly",
}


def _default_question() -> str:
    return "Which items look safest to review on the website right now?"


def _timing_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _extract_json_text(response) -> str:
    text = getattr(response, "output_text", "") or ""
    return text.strip() if text else ""


def _normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_free_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", _normalize_name(value))


def _tokenize(value: str) -> list[str]:
    return [token for token in _normalize_free_text(value).split() if token]


def _safe_int(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _format_gp(value: Any) -> str:
    return f"{_safe_int(value):,} gp"


def _clean_user_prompt(user_message: str) -> str:
    text = str(user_message or "").strip()
    if not text:
        return ""
    return re.split(
        r"\n\s*Current trade decisions:|\n\s*Slot purpose settings:|\n\s*User decision context:",
        text,
        maxsplit=1,
    )[0].strip()


def _load_market_items() -> list[dict[str, Any]]:
    cache = load_market_cache()
    items = cache.get("items", []) if isinstance(cache, dict) else []
    return items if isinstance(items, list) else []


def _item_aliases(name: str) -> set[str]:
    aliases = { _normalize_free_text(name) }
    tokens = aliases.copy()
    for alias in list(tokens):
        parts = alias.split()
        if parts:
            last = parts[-1]
            if last.endswith("s") and len(last) > 3:
                aliases.add(" ".join(parts[:-1] + [last[:-1]]))
            else:
                aliases.add(" ".join(parts[:-1] + [last + "s"]))
    return {a for a in aliases if a}


def _item_match_score(prompt: str, item_name: str) -> int:
    prompt_norm = _normalize_free_text(prompt)
    prompt_tokens = set(_tokenize(prompt))
    item_norm = _normalize_free_text(item_name)
    if not item_norm:
        return -1
    item_tokens = [t for t in item_norm.split() if t not in STOPWORDS]
    if not item_tokens:
        return -1

    score = 0
    aliases = _item_aliases(item_name)
    if any(alias in prompt_norm for alias in aliases):
        longest = max(len(alias) for alias in aliases if alias in prompt_norm)
        score += 20000 + longest

    overlap = len(set(item_tokens) & prompt_tokens)
    if overlap:
        score += overlap * 500
        if overlap == len(item_tokens):
            score += 5000
        elif overlap >= max(1, math.ceil(len(item_tokens) * 0.7)):
            score += 1800

    if item_norm in prompt_norm:
        score += 6000
    elif len(item_tokens) == 1 and item_tokens[0] in prompt_tokens:
        score += 2500

    return score


def _resolve_market_items(prompt: str, limit: int = 4) -> list[dict[str, Any]]:
    items = _load_market_items()
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        score = _item_match_score(prompt, name)
        if score < 2200:
            continue
        scored.append((score, _normalize_name(name), item))
    scored.sort(key=lambda row: (-row[0], row[1]))

    unique: list[tuple[int, dict[str, Any]]] = []
    seen: set[str] = set()
    for score, key, item in scored:
        if key in seen:
            continue
        seen.add(key)
        unique.append((score, item))
        if len(unique) >= limit:
            break

    if not unique:
        return []

    if len(unique) == 1:
        return [unique[0][1]]

    top_score = unique[0][0]
    second_score = unique[1][0]
    if top_score >= 20000 and second_score < 12000:
        return [unique[0][1]]
    if top_score >= 10000 and second_score <= int(top_score * 0.55):
        return [unique[0][1]]

    strong = [item for score, item in unique if score >= max(2500, int(top_score * 0.6))]
    return strong[:limit]


def _safe_decision(raw: Any) -> str:
    value = str(raw or "review").strip().lower()
    allowed = {"consider", "watch", "review", "hold", "avoid"}
    return value if value in allowed else "review"


def _safe_reason(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Useful to review, but full execution stays in the plugin."
    return text[:120]


def _trim_summary(raw: Any, limit: int = 420) -> str:
    text = " ".join(str(raw or "").strip().split())
    if not text:
        return "Good items to review, but the plugin handles the full plan."
    if len(text) <= limit:
        return text
    clipped = text[:limit].rstrip()
    last_break = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
    if last_break >= max(120, limit // 2):
        return clipped[: last_break + 1].strip()
    last_space = clipped.rfind(" ")
    if last_space >= max(100, limit // 2):
        return (clipped[:last_space].rstrip(" ,;:-") + "…").strip()
    return clipped.rstrip(" ,;:-") + "…"


def _extract_minutes(prompt: str) -> int:
    text = _normalize_name(prompt)
    match = re.search(r"(\d{1,4})\s*(?:m|min|mins|minute|minutes)\b", text)
    if match:
        return max(1, min(24 * 60, _safe_int(match.group(1))))
    return 0


def _effective_window_minutes(prompt: str) -> int:
    minutes = _extract_minutes(prompt)
    if minutes > 0:
        return minutes
    hours = _extract_hours(prompt)
    if hours > 0:
        return hours * 60
    return 0


def _window_label(prompt: str, fallback_hours: int = 12) -> str:
    minutes = _extract_minutes(prompt)
    if minutes > 0:
        return f"{minutes}m"
    hours = _extract_hours(prompt)
    if hours > 0:
        return f"{hours}h"
    return f"{fallback_hours}h"


def _question_wants_fast_fill(prompt: str) -> bool:
    text = _normalize_name(prompt)
    phrases = (
        "don't want to wait",
        "do not want to wait",
        "not wait too long",
        "soon",
        "quickly",
        "faster",
        "fast fill",
        "fill quickly",
        "next 30 minutes",
        "next 15 minutes",
        "within 30 minutes",
        "within 15 minutes",
        "within the hour",
        "right away",
        "not instant buy",
        "without instant buying",
    )
    return any(phrase in text for phrase in phrases)


def _question_wants_instant(prompt: str) -> bool:
    text = _normalize_name(prompt)
    return any(phrase in text for phrase in ("instant buy", "instabuy", "insta buy", "buy instantly", "right now no matter what"))


def _pro_hint(item_name: str, flavor: str = "compare") -> str:
    item = str(item_name or "this item").strip()
    compare_hints = [
        f"I’d normally compare {item} against your other active slots in the plugin before deciding whether it deserves more capital.",
        f"I’d usually sanity-check {item} against a few faster movers in the plugin before locking in the bigger rotation.",
        f"On Pro I’d also rank {item} against the rest of your live slots to see whether another item is giving cleaner fills.",
    ]
    timing_hints = [
        f"I’d normally check that entry against your other live slots in the plugin before deciding where the short-window capital should go.",
        f"In Pro I’d compare that urgency tradeoff against your other items before choosing the best fast-fill rotation.",
        f"I’d usually line that timing up against your active slots in the plugin before deciding whether patience or speed wins here.",
    ]
    savings_hints = [
        f"I’d normally compare that patient entry against your other active slots in the plugin before choosing the best full rotation.",
        f"On Pro I’d weigh that cheaper entry against your other slot opportunities before deciding where the slower capital belongs.",
        f"I’d usually compare that saving-vs-speed tradeoff against your other live slots in the plugin before committing the longer hold.",
    ]
    mapping = {"compare": compare_hints, "timing": timing_hints, "savings": savings_hints}
    options = mapping.get(flavor, compare_hints)
    seed = sum(ord(c) for c in _normalize_name(item))
    return options[seed % len(options)]


def _sanitize_reply(reply_json: dict[str, Any], fallback_picks: list[str]) -> dict[str, Any]:
    summary = _trim_summary(reply_json.get("summary"))
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

    top_picks: list[str] = []
    for value in reply_json.get("top_picks", []) or []:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in top_picks:
            top_picks.append(cleaned[:60])
        if len(top_picks) >= MAX_PICKS:
            break
    if not top_picks:
        top_picks = fallback_picks[:MAX_PICKS]

    notes: list[str] = []
    for value in reply_json.get("notes", []) or []:
        cleaned = str(value or "").strip()
        if cleaned:
            notes.append(cleaned[:160])
        if len(notes) >= 2:
            break
    if not any("plugin" in note.lower() for note in notes):
        notes.append("The plugin is where the broader slot-by-slot roadmap and replacement logic lives.")

    return {
        "summary": summary,
        "actions": actions,
        "top_picks": top_picks[:MAX_PICKS],
        "notes": notes[:3],
        "mode": str(reply_json.get("mode") or "web_safe"),
        "render_style": str(reply_json.get("render_style") or ""),
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


def _extract_hours(prompt: str) -> int:
    text = _normalize_name(prompt)
    match = re.search(r"(\d{1,3})\s*(?:h|hr|hrs|hour|hours)", text)
    if match:
        return max(1, min(48, _safe_int(match.group(1))))
    if "overnight" in text:
        return 8
    if "all day" in text:
        return 12
    return 0


def _quantity_alias_patterns(item_name: str) -> list[str]:
    aliases = sorted(_item_aliases(item_name), key=len, reverse=True)
    escaped = [re.escape(alias) for alias in aliases if alias]
    return escaped


def _extract_target_quantity(prompt: str, item_name: str = "") -> int:
    text = _normalize_free_text(_clean_user_prompt(prompt))
    if not text:
        return 0

    blocked_units = {
        "m", "min", "mins", "minute", "minutes", "h", "hr", "hrs", "hour", "hours",
        "gp", "k", "mil", "mill", "million", "billion", "budget",
    }

    quantity_patterns = []
    if item_name:
        alias_group = "(?:" + "|".join(_quantity_alias_patterns(item_name)) + ")"
        quantity_patterns.extend([
            rf"\b(?:buy|sell|flip|get|fill|move|offer|pick up|acquire|grab)\s+(\d[\d,]*)\s+(?:of\s+)?{alias_group}\b",
            rf"\b(\d[\d,]*)\s+(?:of\s+)?{alias_group}\b",
        ])

    quantity_patterns.extend([
        r"\b(?:buy|sell|flip|get|fill|move|offer|pick up|acquire|grab)\s+(\d[\d,]*)\s+(units?|ea|each)\b",
        r"\bquantity\s+(?:of\s+)?(\d[\d,]*)\b",
        r"\bqty\s+(\d[\d,]*)\b",
    ])

    for pattern in quantity_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = _safe_int(match.group(1).replace(",", ""))
        if 0 < value <= 100_000_000:
            return value

    for match in re.finditer(r"\b(\d[\d,]*)\s+([a-z]+)\b", text):
        value = _safe_int(match.group(1).replace(",", ""))
        unit = match.group(2)
        if value <= 0 or value > 100_000_000 or unit in blocked_units:
            continue
        if item_name and unit in set(_tokenize(item_name)):
            return value

    return 0


def _question_wants_savings(prompt: str) -> bool:
    text = _normalize_name(prompt)
    return any(phrase in text for phrase in ("save money", "cheapest", "cheap", "lowest", "gradually", "over a period", "over time", "bulk", "in bulk"))


def _classify_query(prompt: str, resolved_items: list[dict[str, Any]]) -> str:
    text = _normalize_name(prompt)
    if any(blocker in text for blocker in NON_OSRS_BLOCKERS):
        return "blocked"
    if any(keyword in text for keyword in PLANNING_KEYWORDS):
        return "portfolio"
    if len(resolved_items) >= 3:
        return "multi_restricted"
    if len(resolved_items) == 2 and any(keyword in text for keyword in COMPARE_KEYWORDS):
        return "dual"
    if len(resolved_items) >= 1:
        return "single"
    if any(keyword in text for keyword in COMPARE_KEYWORDS):
        return "dual"
    return "general"


def _speed_label(volume_1h: int) -> str:
    if volume_1h >= 100_000:
        return "very fast"
    if volume_1h >= 25_000:
        return "fast"
    if volume_1h >= 5_000:
        return "steady"
    if volume_1h > 0:
        return "slower"
    return "unknown"



def _cache_first_market_snapshot(item_name: str) -> dict[str, Any]:
    normalized_target = _normalize_name(item_name)
    best_item: dict[str, Any] | None = None
    best_score = -1
    for item in _load_market_items():
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        score = _item_match_score(normalized_target, name)
        if score > best_score:
            best_score = score
            best_item = item
    if not best_item:
        return {"found": False, "item_name": item_name}

    def pick(*keys: str, default: Any = 0) -> Any:
        for key in keys:
            if key in best_item and best_item.get(key) not in (None, ""):
                return best_item.get(key)
        return default

    buy_price = _safe_int(pick("buy_price", "buy", "instant_buy", "current_buy", default=0))
    sell_price = _safe_int(pick("sell_price", "sell", "instant_sell", "current_sell", default=0))
    if buy_price <= 0 and sell_price > 0:
        buy_price = max(1, sell_price - max(1, _safe_int(pick("spread", default=1))))
    if sell_price <= 0 and buy_price > 0:
        sell_price = buy_price + max(1, _safe_int(pick("spread", default=1)))
    spread = _safe_int(pick("spread", default=max(0, sell_price - buy_price)))
    if spread <= 0 and buy_price > 0 and sell_price > 0:
        spread = max(0, sell_price - buy_price)

    volume_5m = _safe_int(pick("volume_5m", "recent_volume_5m", "five_minute_volume", "5m_volume", default=0))
    volume_1h = _safe_int(pick("recent_volume_1h", "volume_1h", "hourly_volume", default=0))
    if volume_1h <= 0 and volume_5m > 0:
        volume_1h = volume_5m * 12
    if volume_5m <= 0 and volume_1h > 0:
        volume_5m = max(1, volume_1h // 12)

    buy_limit = _safe_int(pick("buy_limit", "limit", default=0))
    high_alch = _safe_int(pick("high_alch", "high_alch_value", default=0))
    day_low = _safe_int(pick("day_low", "low", default=0))
    day_high = _safe_int(pick("day_high", "high", default=0))
    if day_low <= 0 and buy_price > 0:
        day_low = buy_price
    if day_high <= 0 and sell_price > 0:
        day_high = sell_price

    canonical_name = str(best_item.get("name") or item_name).strip()
    roi_pct = round((_safe_float(spread) / buy_price) * 100, 3) if buy_price > 0 and spread > 0 else 0.0
    tax_estimate = max(1, int(round(sell_price * 0.01))) if sell_price > 0 else 0
    after_tax_profit = max(0, spread - tax_estimate) if spread > 0 else 0
    week_position_pct = 0.0
    if day_high > day_low and buy_price > 0:
        week_position_pct = round(max(0.0, min(100.0, ((buy_price - day_low) / max(1, (day_high - day_low))) * 100)), 1)

    if spread >= max(8, buy_price * 0.015):
        entry_signal = "favorable"
    elif spread > 0:
        entry_signal = "mixed"
    else:
        entry_signal = "tight"

    return {
        "found": True,
        "item_name": canonical_name,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "spread": spread,
        "profit_per_item": after_tax_profit,
        "volume_5m": volume_5m,
        "recent_volume_1h": volume_1h,
        "buy_limit": buy_limit,
        "roi_pct": roi_pct,
        "day_low": day_low,
        "day_high": day_high,
        "week_position_pct": week_position_pct,
        "entry_signal": entry_signal,
        "high_alch_value": high_alch,
        "history_skipped": True,
    }


def _single_item_reply(prompt: str, item_name: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    market = _cache_first_market_snapshot(item_name)
    if not market.get("found"):
        return None, {"resolved_item": item_name, "found": False, "cache_only": True}

    canonical_name = str(market.get("item_name") or item_name).strip()
    buy_price = _safe_int(market.get("buy_price"))
    sell_price = _safe_int(market.get("sell_price"))
    spread = _safe_int(market.get("spread"))
    profit = _safe_int(market.get("profit_per_item"))
    volume_5m = _safe_int(market.get("volume_5m"))
    volume_1h = _safe_int(market.get("recent_volume_1h"))
    buy_limit = _safe_int(market.get("buy_limit"))
    roi_pct = round(_safe_float(market.get("roi_pct")), 3)
    day_low = _safe_int(market.get("day_low"))
    day_high = _safe_int(market.get("day_high"))
    week_pos = round(_safe_float(market.get("week_position_pct")), 1)
    entry_signal = str(market.get("entry_signal") or "mixed").replace("_", " ")
    hours = _extract_hours(prompt)
    minutes = _extract_minutes(prompt)
    window_minutes = _effective_window_minutes(prompt)
    window_label = _window_label(prompt)
    target_qty = _extract_target_quantity(prompt, canonical_name)
    wants_savings = _question_wants_savings(prompt)
    wants_fast_fill = _question_wants_fast_fill(prompt)
    wants_instant = _question_wants_instant(prompt)
    prompt_text = _normalize_name(prompt)

    if buy_price <= 0 and sell_price <= 0:
        return None, {"resolved_item": canonical_name, "found": False, "cache_only": True, "reason": "missing_live_prices"}

    patient_bid = max(1, day_low) if day_low > 0 and (buy_price <= 0 or day_low <= buy_price) else max(1, buy_price)
    slightly_aggressive_bid = max(patient_bid, buy_price + max(1, spread // 5)) if buy_price > 0 else patient_bid
    slightly_aggressive_bid = min(max(1, sell_price - 1), slightly_aggressive_bid) if sell_price > 0 else slightly_aggressive_bid
    instant_cap_bid = max(patient_bid, buy_price + max(1, spread // 2)) if buy_price > 0 else patient_bid
    instant_cap_bid = min(max(1, sell_price - 1), instant_cap_bid) if sell_price > 0 else instant_cap_bid

    render_style = "natural_paragraph"
    hint_flavor = "compare"

    if any(token in prompt_text for token in ("how many", "quantity", "qty")) and window_minutes > 0:
        hours_window = max(1, math.ceil(window_minutes / 60))
        reset_cycles = max(1, math.ceil(hours_window / 4))
        personal_cap = buy_limit * reset_cycles if buy_limit > 0 else 0
        market_cap = volume_1h * hours_window if volume_1h > 0 else 0
        candidates = [v for v in (personal_cap, market_cap) if v > 0]
        likely_cap = min(candidates) if len(candidates) == 2 else (candidates[0] if candidates else 0)
        if spread <= 0:
            summary = (
                f"{canonical_name} looks effectively flat right now at about {_format_gp(buy_price)} buy / {_format_gp(sell_price)} sell. "
                f"The GE limit is about {buy_limit:,} every 4 hours" + (f", so roughly {buy_limit * reset_cycles:,} across {window_label}" if buy_limit > 0 else f" across {window_label}") + ". "
                f"I would start near {_format_gp(max(1, patient_bid))} per item and only pay up if the live spread opens again."
            )
        elif likely_cap > 0:
            summary = (
                f"{canonical_name} can probably move around {likely_cap:,} in {window_label} if fills stay normal. "
                f"The GE limit is about {buy_limit:,} every 4 hours" if buy_limit > 0 else f"{canonical_name} can probably move around {likely_cap:,} in {window_label} if fills stay normal. "
            )
            if buy_limit > 0:
                summary += f", so your own cap is roughly {buy_limit * reset_cycles:,} across that window. "
            summary += f"I would start around {_format_gp(patient_bid)} per item and only nudge higher if fills are too slow."
        else:
            summary = (
                f"{canonical_name} needs a quick live fill-speed check before sizing a {window_label} window. "
                f"Right now it is around {_format_gp(buy_price)} buy / {_format_gp(sell_price)} sell, so I would start near {_format_gp(patient_bid)} and scale only after fills confirm."
            )
        hint_flavor = "timing"
    elif target_qty > 0 and (wants_savings or window_minutes >= 360) and not wants_fast_fill and not wants_instant:
        patience_note = (
            f"{target_qty:,} units is tiny versus roughly {volume_1h:,}/h turnover"
            if volume_1h > 0 else
            f"{target_qty:,} units is a small enough target to stay patient"
        )
        summary = (
            f"For {target_qty:,} {canonical_name} over {window_label}, I would start around {_format_gp(patient_bid)} and let it fill rather than chase higher right away. "
            f"{patience_note}, so saving money matters more than instant execution unless the market starts moving away from you."
        )
        if slightly_aggressive_bid > patient_bid:
            summary += f" If the order sits too long, nudging toward {_format_gp(slightly_aggressive_bid)} is still reasonable."
        if spread > 0 and sell_price > 0:
            summary += f" The live spread is about {spread:,} gp with roughly {_format_gp(sell_price)} on the sell side."
        hint_flavor = "savings"
    elif ("buy and sell" in prompt_text or "max profit" in prompt_text or "flip" in prompt_text) and buy_price > 0 and sell_price > 0:
        entry_bid = patient_bid if window_minutes >= 240 or wants_savings else max(patient_bid, slightly_aggressive_bid)
        exit_target = max(entry_bid + 1, sell_price - max(1, spread // 4)) if spread > 0 else sell_price
        summary = (
            f"{canonical_name} is around {_format_gp(buy_price)} buy / {_format_gp(sell_price)} sell right now. "
            f"For a {window_label} flip, I would look to buy near {_format_gp(entry_bid)} and sell back closer to {_format_gp(exit_target)} so the spread still leaves room after tax. "
            f"With a longer window, you can stay patient on the entry rather than chasing every uptick."
        )
        hint_flavor = "compare"
    elif window_minutes > 0 and (wants_fast_fill or wants_instant or window_minutes <= 90):
        if wants_instant:
            start_bid = instant_cap_bid
            posture = "If you want it quickly, you will need to bid into the spread rather than sit right on the floor."
        else:
            start_bid = slightly_aggressive_bid
            posture = "With a short window, sitting exactly on the floor is more likely to leave part of the order hanging."
        qty_phrase = f" for about {target_qty:,} units" if target_qty > 0 else ""
        summary = (
            f"{canonical_name} is around {_format_gp(buy_price)} buy / {_format_gp(sell_price)} sell right now. "
            f"For a {window_label} bulk entry{qty_phrase}, I would start around {_format_gp(start_bid)} so you get fills without drifting all the way into an instant buy. "
            f"{posture} If it still does not move after a few minutes, stepping up slightly is usually better than waiting out the whole window."
        )
        if spread > 0:
            summary += f" The spread is about {spread:,} gp, so overpaying too far quickly eats the edge."
        hint_flavor = "timing"
    else:
        speed_phrase = (
            f"and about {volume_1h:,}/h turnover"
            if volume_1h > 0 else
            "with live prices still worth checking against fill speed"
        )
        summary = (
            f"{canonical_name} is around {_format_gp(buy_price)} buy / {_format_gp(sell_price)} sell right now. "
            f"If you are buying in bulk, I would try to enter near {_format_gp(patient_bid)} and avoid chasing too far into the spread unless fills are unusually fast. "
            f"At roughly {spread:,} gp spread, selling near {_format_gp(sell_price)} leaves about {profit:,} gp after tax per item, {speed_phrase}."
        )
        hint_flavor = "compare"

    notes = [_pro_hint(canonical_name, flavor=hint_flavor)]

    reply = _sanitize_reply(
        {
            "summary": summary,
            "actions": [],
            "top_picks": [],
            "notes": notes,
            "mode": "single_item_cache_fast_path",
            "render_style": render_style,
        },
        [],
    )
    debug = {
        "resolved_item": canonical_name,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "spread": spread,
        "profit_per_item": profit,
        "volume_5m": volume_5m,
        "recent_volume_1h": volume_1h,
        "buy_limit": buy_limit,
        "roi_pct": roi_pct,
        "day_low": day_low,
        "day_high": day_high,
        "week_position_pct": week_pos,
        "entry_signal": entry_signal,
        "hours": hours,
        "minutes": minutes,
        "window_minutes": window_minutes,
        "target_quantity": target_qty,
        "wants_savings": wants_savings,
        "wants_fast_fill": wants_fast_fill,
        "wants_instant": wants_instant,
        "cache_only": True,
        "history_skipped": True,
    }
    return reply, debug

def _dual_item_reply(prompt: str, items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    contexts = []
    for item in items[:2]:
        ctx = build_ai_context_for_query(str(item.get("name") or ""))
        if ctx.get("found"):
            contexts.append(ctx)
    if len(contexts) < 2:
        return None, {"resolved_items": [str(item.get("name") or "") for item in items[:2]], "found_count": len(contexts)}

    a, b = contexts[0], contexts[1]
    av = _safe_int((a.get("current_market", {}) or {}).get("recent_volume_1h"))
    bv = _safe_int((b.get("current_market", {}) or {}).get("recent_volume_1h"))
    ap = _safe_int((a.get("current_market", {}) or {}).get("profit_per_item"))
    bp = _safe_int((b.get("current_market", {}) or {}).get("profit_per_item"))
    steadier = a if av >= bv else b
    higher_edge = a if ap >= bp else b
    summary = f"For a limited two-item check, {steadier['item_name']} looks steadier while {higher_edge['item_name']} has the better per-item edge."
    reply = _sanitize_reply(
        {
            "summary": summary,
            "actions": [
                {
                    "item": a["item_name"],
                    "decision": "review",
                    "reason": f"About {_format_gp((a.get('current_market', {}) or {}).get('buy_price'))} buy / {_format_gp((a.get('current_market', {}) or {}).get('sell_price'))} sell right now.",
                },
                {
                    "item": b["item_name"],
                    "decision": "review",
                    "reason": f"About {_format_gp((b.get('current_market', {}) or {}).get('buy_price'))} buy / {_format_gp((b.get('current_market', {}) or {}).get('sell_price'))} sell right now.",
                },
            ],
            "top_picks": [steadier["item_name"], higher_edge["item_name"]],
            "notes": [
                "I can compare one or two items here, but the plugin is where the full slot ranking kicks in.",
            ],
            "mode": "dual_item_market_data",
        },
        [steadier["item_name"], higher_edge["item_name"]],
    )
    return reply, {
        "resolved_items": [a["item_name"], b["item_name"]],
        "steadier_item": steadier["item_name"],
        "higher_edge_item": higher_edge["item_name"],
    }


def _premium_boundary_reply(resolved_names: list[str]) -> dict[str, Any]:
    return _sanitize_reply(
        {
            "summary": "I can help with one or two item checks here, but full multi-item planning stays in the plugin.",
            "actions": [
                {
                    "item": resolved_names[0] if resolved_names else "Multi-item planning",
                    "decision": "review",
                    "reason": "The free copilot is for narrow checks, not full slot-by-slot optimization.",
                }
            ],
            "top_picks": resolved_names[:2],
            "notes": [
                "The plugin is where you get the broader roadmap, replacements, and faster 8-slot decisions.",
            ],
            "mode": "premium_boundary",
        },
        resolved_names[:2],
    )



def _is_restricted_planning_prompt(prompt: str) -> bool:
    text = _normalize_name(prompt)
    planning_phrases = (
        "anchor slot",
        "anchor slots",
        "manage two anchor",
        "manage my anchor",
        "best manage",
        "slot strategy",
        "slot strategies",
        "regardless of budget",
        "for 8 hours away",
        "for 12 hours away",
    )
    if any(phrase in text for phrase in planning_phrases):
        return True
    return ("slot" in text and ("away" in text or "hours" in text)) or ("anchor" in text and "slot" in text)


def _restricted_planning_reply(prompt: str) -> dict[str, Any]:
    hours = _extract_hours(prompt) or 8
    label = f"{hours}h"
    summary = (
        f"For a {label} anchor-slot window, keep those slots focused on steadier, lower-maintenance holds rather than forcing aggressive rotations."
    )
    actions = [
        {
            "item": "Anchor slots",
            "decision": "review",
            "reason": "Use them for steadier fills and less babysitting while you are away.",
        },
        {
            "item": "Higher-volatility ideas",
            "decision": "avoid",
            "reason": "Save faster rotations for the plugin where full slot management is available.",
        },
    ]
    notes = [
        "I can preview anchor-slot tradeoffs here, but broader multi-slot planning stays in the plugin.",
    ]
    return _sanitize_reply(
        {
            "summary": summary,
            "actions": actions,
            "top_picks": [],
            "notes": notes,
            "mode": "restricted_planning_fast_path",
        },
        [],
    )


def _natural_fallback_reply(user_message: str, recommendations: dict[str, Any]) -> dict[str, Any]:
    prompt = _clean_user_prompt(user_message).lower()
    summary = "Here is the quick preview I can give from your OSRS trade context."
    actions: list[dict[str, str]] = []
    notes: list[str] = []
    if "12" in prompt or "away" in prompt or "overnight" in prompt:
        summary = "For a longer offline window, remove slower or thinner trades first."
        actions = [
            {"item": "Slowest-moving trades", "decision": "review", "reason": "Most likely to sit idle while you are away."},
            {"item": "Low-margin trades", "decision": "avoid", "reason": "They have less room for drift over time."},
        ]
        notes = ["Steadier, higher-volume items are usually safer when you cannot actively adjust."]
    elif "weak" in prompt or "weakest" in prompt or "replace" in prompt or "cancel" in prompt:
        summary = "I would inspect the weakest slots by movement speed and margin first."
        actions = [
            {"item": "Slow-moving slots", "decision": "review", "reason": "Idle slots tie up budget without much upside."},
            {"item": "Thin-margin holds", "decision": "watch", "reason": "Small edge disappears quickly when prices move."},
        ]
        notes = ["Weak trades usually combine low velocity, low edge, or constant repricing pressure."]
    elif "risk" in prompt:
        summary = "Lower risk usually means favoring steadier fills over sharper but fragile margins."
        actions = [
            {"item": "High-volatility slots", "decision": "review", "reason": "They need more babysitting to stay efficient."},
            {"item": "Steadier volume items", "decision": "consider", "reason": "They tend to be more forgiving over time."},
        ]
        notes = ["You do not need to abandon profit entirely to smooth out risk."]
    else:
        actions = [
            {"item": "Slowest-moving slots", "decision": "review", "reason": "They are the first places I would inspect."},
            {"item": "Budget concentration", "decision": "watch", "reason": "Too much tied to one idea increases downside."},
        ]
        notes = ["I can still help with priorities, tradeoffs, and which trades look safest to review next."]
    picks = []
    for item in (recommendations.get("recommendations", []) or []):
        name = str(item.get("name") or "").strip()
        if name and name not in picks:
            picks.append(name)
        if len(picks) >= 3:
            break
    return _sanitize_reply({"summary": summary, "actions": actions, "top_picks": picks, "notes": notes}, picks)


def _fast_path_reply(user_message: str) -> dict[str, Any] | None:
    prompt = _clean_user_prompt(user_message)
    payload = FAST_PATH_PRESET_REPLIES.get(prompt.lower())
    if not payload:
        return None
    reply_json = _sanitize_reply(
        {
            "summary": payload.get("summary"),
            "actions": payload.get("actions", []),
            "top_picks": [],
            "notes": payload.get("notes", []),
            "mode": "web_fast_path",
        },
        [],
    )
    reply_json["mode"] = "web_fast_path"
    return reply_json


def _recommendation_counts(recommendations: dict[str, Any]) -> dict[str, int]:
    return {bucket: len(recommendations.get(bucket, []) or []) for bucket in ("recommendations", "high_value", "overnight", "anchors", "dump")}


def _recommendation_preview(recommendations: dict[str, Any]) -> list[str]:
    preview: list[str] = []
    for bucket in ("recommendations", "high_value", "overnight", "anchors", "dump"):
        for item in recommendations.get(bucket, []) or []:
            name = str(item.get("name") or "").strip()
            if name and name not in preview:
                preview.append(name)
            if len(preview) >= 5:
                return preview
    return preview


def _build_debug_base(*, settings: dict[str, Any], prompt: str, model: str) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "requested_model": model,
        "timeout_seconds": AI_TIMEOUT_SECONDS,
        "settings_snapshot": {
            "budget": settings.get("budget", 0),
            "available_slots": settings.get("available_slots", 0),
            "hours_away": settings.get("hours_away", 0),
        },
    }


def build_ai_advice(user_message: str) -> dict[str, Any]:
    total_start = time.perf_counter()
    settings = load_settings()
    prompt = _clean_user_prompt(user_message) or _default_question()
    model = settings.get("openai_model", DEFAULT_MODEL) or DEFAULT_MODEL
    debug = _build_debug_base(settings=settings, prompt=prompt, model=model)

    fast_path_reply = _fast_path_reply(prompt)
    if fast_path_reply:
        debug.update({"mode": "preset_fast_path", "openai_called": False, "matched_preset": prompt.lower(), "total_ms": _timing_ms(total_start)})
        return {
            "model": "preset-fast-path",
            "reply_json": fast_path_reply,
            "raw_reply": "",
            "current_scan": {"offers": []},
            "decisions": {"decisions": []},
            "recommendations": {},
            "debug": debug,
        }

    if _is_restricted_planning_prompt(prompt):
        reply_json = _restricted_planning_reply(prompt)
        debug.update({"mode": "restricted_planning_fast_path", "openai_called": False, "total_ms": _timing_ms(total_start)})
        return {"model": "restricted-planning-fast-path", "reply_json": reply_json, "raw_reply": "", "current_scan": {"offers": []}, "decisions": {"decisions": []}, "recommendations": {}, "debug": debug}

    resolved_items = _resolve_market_items(prompt, limit=4)
    resolved_names = [str(item.get("name") or "").strip() for item in resolved_items]
    classification = _classify_query(prompt, resolved_items)
    debug.update({
        "classification": classification,
        "resolved_items": resolved_names,
        "market_cache_items": len(_load_market_items()),
    })

    if classification == "blocked":
        reply = _sanitize_reply(
            {
                "summary": "I can only help with OSRS market questions, item checks, and trade decisions here.",
                "actions": [{"item": "OSRS markets", "decision": "review", "reason": "Try a Grand Exchange item, spread, margin, or timing question."}],
                "top_picks": [],
                "notes": ["The plugin is focused on GE data, flips, and slot optimization."],
                "mode": "domain_block",
            },
            [],
        )
        debug.update({"mode": "domain_block", "openai_called": False, "total_ms": _timing_ms(total_start)})
        return {"model": "guard-domain-block", "reply_json": reply, "raw_reply": "", "current_scan": {"offers": []}, "decisions": {"decisions": []}, "recommendations": {}, "debug": debug}

    if classification == "single" and resolved_names:
        reply_json, single_debug = _single_item_reply(prompt, resolved_names[0])
        if reply_json:
            debug.update({"mode": "single_item_cache_fast_path", "openai_called": False, "single_item": single_debug, "total_ms": _timing_ms(total_start)})
            return {"model": "market-fast-path", "reply_json": reply_json, "raw_reply": "", "current_scan": {"offers": []}, "decisions": {"decisions": []}, "recommendations": {}, "debug": debug}

    if classification == "dual" and len(resolved_names) >= 2:
        reply_json, dual_debug = _dual_item_reply(prompt, resolved_items[:2])
        if reply_json:
            debug.update({"mode": "dual_item_market_fast_path", "openai_called": False, "dual_item": dual_debug, "total_ms": _timing_ms(total_start)})
            return {"model": "market-fast-path", "reply_json": reply_json, "raw_reply": "", "current_scan": {"offers": []}, "decisions": {"decisions": []}, "recommendations": {}, "debug": debug}

    if classification in {"multi_restricted", "portfolio"}:
        reply_json = _premium_boundary_reply(resolved_names)
        debug.update({"mode": "premium_boundary", "openai_called": False, "total_ms": _timing_ms(total_start)})
        return {"model": "premium-boundary", "reply_json": reply_json, "raw_reply": "", "current_scan": {"offers": []}, "decisions": {"decisions": []}, "recommendations": {}, "debug": debug}

    prep_start = time.perf_counter()
    current_scan = get_current_offers()
    decisions = build_trade_decisions(settings=settings, current_scan=current_scan)
    recommendations = build_recommendations(settings=settings, current_scan=current_scan, mode="web_safe")
    debug.update({
        "prep_ms": _timing_ms(prep_start),
        "current_offer_count": len(current_scan.get("offers", []) or []),
        "decision_count": len(decisions.get("decisions", []) or []),
        "recommendation_counts": _recommendation_counts(recommendations),
        "recommendation_preview": _recommendation_preview(recommendations),
    })

    api_key = resolve_openai_api_key(settings)
    if not api_key:
        debug.update({"mode": "no_api_key_fallback", "openai_called": False, "total_ms": _timing_ms(total_start)})
        return {
            "model": "fallback-web-safe",
            "reply_json": _natural_fallback_reply(prompt, recommendations),
            "raw_reply": "",
            "current_scan": current_scan,
            "decisions": decisions,
            "recommendations": recommendations,
            "debug": debug,
        }

    try:
        openai_start = time.perf_counter()
        client = OpenAI(api_key=api_key, timeout=AI_TIMEOUT_SECONDS)
        response = client.responses.create(
            model=model,
            max_output_tokens=180,
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
                        "At most 2 actions, 3 top_picks, and 2 notes before the plugin reminder. "
                        "Keep summary under 22 words. "
                        "Keep each reason under 14 words. "
                        "Sound like a real AI giving a limited preview, not a scripted block. "
                        "Frame the plugin as the place for full optimization and execution guidance."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        debug["openai_ms"] = _timing_ms(openai_start)
        debug["openai_called"] = True
        debug["response_id"] = getattr(response, "id", None)
    except Exception as e:
        debug.update({
            "mode": "openai_exception_fallback",
            "openai_called": True,
            "error_type": type(e).__name__,
            "error_message": str(e),
            "total_ms": _timing_ms(total_start),
        })
        return {
            "error": f"AI request failed on model {model}: {str(e)}",
            "reply_json": _natural_fallback_reply(prompt, recommendations),
            "model": f"{model}-fallback",
            "debug": debug,
        }

    raw_text = _extract_json_text(response)
    if not raw_text:
        debug.update({"mode": "empty_response_fallback", "raw_reply_chars": 0, "total_ms": _timing_ms(total_start)})
        return {
            "model": f"{model}-fallback",
            "reply_json": _natural_fallback_reply(prompt, recommendations),
            "raw_reply": "",
            "current_scan": current_scan,
            "decisions": decisions,
            "recommendations": recommendations,
            "debug": debug,
        }

    fallback_picks = _recommendation_preview(recommendations)[:3]
    reply_json = _coerce_json_reply(raw_text, fallback_picks)
    debug.update({
        "mode": "openai_success",
        "raw_reply_chars": len(raw_text),
        "reply_mode": reply_json.get("mode"),
        "total_ms": _timing_ms(total_start),
    })

    return {
        "model": model,
        "reply_json": reply_json,
        "raw_reply": raw_text,
        "current_scan": current_scan,
        "decisions": decisions,
        "recommendations": recommendations,
        "debug": debug,
    }
