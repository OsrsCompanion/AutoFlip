from __future__ import annotations

from typing import Any

from app.services.wiki_prices import get_market_snapshot

GE_TAX_RATE = 0.02
GE_TAX_CAP_PER_ITEM = 5_000_000


def _normalize_name(name: str) -> str:
    return " ".join((name or "").lower().strip().split())


def _sell_tax_per_item(sell_price: int) -> int:
    return min(int(sell_price * GE_TAX_RATE), GE_TAX_CAP_PER_ITEM)


def _after_tax_profit_per_item(buy_price: int, sell_price: int) -> int:
    return sell_price - _sell_tax_per_item(sell_price) - buy_price


def _hours_profile(hours_away: int) -> str:
    if hours_away <= 1:
        return "fast"
    if hours_away <= 4:
        return "medium"
    return "slow"


def _build_market_lookup() -> dict[str, dict[str, Any]]:
    items = get_market_snapshot().get("items", [])
    return {_normalize_name(item.get("name", "")): item for item in items if item.get("name")}


def _is_completed_offer(offer: dict[str, Any]) -> bool:
    state = str(offer.get("state", "") or "").lower()
    status = str(offer.get("status_text", "") or "").lower()
    quantity = str(offer.get("quantity_text", "") or "").strip()

    if state in {"sold", "bought", "completed"}:
        return True
    if "sold" in status or "bought" in status or "complete" in status:
        return True
    if "/" in quantity:
        try:
            left, right = [part.strip() for part in quantity.split("/", 1)]
            if left.isdigit() and right.isdigit() and int(right) > 0 and int(left) >= int(right):
                return True
        except Exception:
            pass
    return False


def _decision_for_offer(offer: dict[str, Any], item: dict[str, Any] | None, hours_away: int) -> dict[str, Any]:
    item_name = offer.get("item_name", "Unknown item")
    offer_state = offer.get("state", "unknown")
    offer_price = int(offer.get("coin_amount", 0) or 0)
    profile = _hours_profile(hours_away)

    if _is_completed_offer(offer):
        return {
            "item_name": item_name,
            "state": offer_state,
            "listed_price": offer_price,
            "decision": "completed",
            "summary": "This slot appears complete.",
            "reason": "completed_offer",
        }

    if item is None:
        return {
            "item_name": item_name,
            "state": offer_state,
            "listed_price": offer_price,
            "decision": "hold",
            "summary": "No market snapshot found for this item yet.",
            "reason": "unmatched_item",
        }

    low_price = int(item.get("low", 0) or 0)
    high_price = int(item.get("high", 0) or 0)

    if offer_state == "buying":
        if offer_price <= 0:
            return {
                "item_name": item_name,
                "state": offer_state,
                "listed_price": offer_price,
                "market_buy_price": low_price,
                "market_sell_price": high_price,
                "decision": "hold",
                "summary": "Buy offer price is missing, so this one needs manual review.",
                "reason": "missing_offer_price",
            }

        gap_to_low = low_price - offer_price

        if gap_to_low > max(2, int(low_price * 0.03)):
            decision = "replace" if profile == "fast" else "cancel"
            summary = "This buy offer is priced too far below the current market and may sit for too long."
        elif gap_to_low > max(1, int(low_price * 0.01)):
            decision = "hold" if profile != "fast" else "replace"
            summary = "This buy offer is slightly under current market. It may fill, but not quickly."
        else:
            decision = "keep"
            summary = "This buy offer is close enough to the current market to keep active."

        return {
            "item_name": item_name,
            "state": offer_state,
            "listed_price": offer_price,
            "market_buy_price": low_price,
            "market_sell_price": high_price,
            "decision": decision,
            "summary": summary,
            "reason": "buy_offer_review",
        }

    if offer_state in {"selling", "sold"}:
        if offer_price <= 0:
            return {
                "item_name": item_name,
                "state": offer_state,
                "listed_price": offer_price,
                "market_buy_price": low_price,
                "market_sell_price": high_price,
                "decision": "hold",
                "summary": "Sell price is missing, so this one needs manual review.",
                "reason": "missing_offer_price",
            }

        profit_per_item = _after_tax_profit_per_item(low_price, offer_price)

        if profit_per_item <= 0:
            decision = "cancel" if profile == "fast" else "replace"
            summary = "After tax, this sell price does not leave a good margin."
        elif offer_price > high_price + max(5, int(high_price * 0.03)):
            decision = "replace" if profile == "fast" else "hold"
            summary = "This sell offer is meaningfully above current market and may take too long to move."
        else:
            decision = "keep"
            summary = "This sell offer still looks reasonable after tax."

        return {
            "item_name": item_name,
            "state": offer_state,
            "listed_price": offer_price,
            "market_buy_price": low_price,
            "market_sell_price": high_price,
            "profit_per_item": profit_per_item,
            "decision": decision,
            "summary": summary,
            "reason": "sell_offer_review",
        }

    return {
        "item_name": item_name,
        "state": offer_state,
        "listed_price": offer_price,
        "market_buy_price": low_price,
        "market_sell_price": high_price,
        "decision": "hold",
        "summary": "State is not fully recognized yet, so keep it for now.",
        "reason": "unknown_state",
    }


def build_trade_decisions(settings: dict[str, Any], current_scan: dict[str, Any]) -> dict[str, Any]:
    hours_away = int(settings.get("hours_away", 1) or 1)
    offers = current_scan.get("offers", [])
    market_lookup = _build_market_lookup()

    decisions: list[dict[str, Any]] = []
    for offer in offers:
        market_item = market_lookup.get(_normalize_name(offer.get("item_name", "")))
        decisions.append(_decision_for_offer(offer=offer, item=market_item, hours_away=hours_away))

    counts = {"keep": 0, "hold": 0, "replace": 0, "cancel": 0, "completed": 0}
    for decision in decisions:
        action = decision.get("decision")
        if action in counts:
            counts[action] += 1

    return {
        "settings": {
            "hours_away": hours_away,
            "profile": _hours_profile(hours_away),
        },
        "current_scan": current_scan,
        "decisions": decisions,
        "decision_counts": counts,
    }
