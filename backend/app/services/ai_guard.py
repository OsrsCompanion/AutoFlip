from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

DEFAULT_REQUESTS_PER_MINUTE = 3
DEFAULT_REQUESTS_PER_HOUR = 18
WINDOW_MINUTE_SECONDS = 60
WINDOW_HOUR_SECONDS = 3600

_DOMAIN_KEYWORDS = {
    "osrs", "runescape", "grand exchange", "ge", "flip", "flipping", "merchant", "merchanting",
    "margin", "margins", "item", "items", "slot", "slots", "offer", "offers", "budget", "gp",
    "roi", "profit", "profits", "volume", "spread", "buy limit", "overnight", "undercut", "liquidity",
    "trade", "trades", "trading", "risk", "review", "hold", "replace", "cancel", "dump", "watch",
}

_BLOCK_PATTERNS = (
    "math", "homework", "algebra", "geometry", "calculus", "equation", "solve ", "solve:",
    "essay", "thesis", "coding interview", "leetcode", "python code", "javascript", "translate",
    "history paper", "science project", "book report", "school assignment",
)

_ALLOWED_STRATEGY_HINTS = (
    "replace", "cancel", "hold", "review", "risk", "budget", "hours", "away", "overnight",
    "slot", "slots", "trade", "trades", "flip", "profit", "margin", "item", "items",
)


class InMemoryAiRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._minute_events: dict[str, deque[float]] = {}
        self._hour_events: dict[str, deque[float]] = {}

    def check_and_record(self, key: str, per_minute: int = DEFAULT_REQUESTS_PER_MINUTE, per_hour: int = DEFAULT_REQUESTS_PER_HOUR) -> dict[str, Any]:
        now = time.time()
        minute_cutoff = now - WINDOW_MINUTE_SECONDS
        hour_cutoff = now - WINDOW_HOUR_SECONDS
        safe_key = str(key or "anonymous").strip() or "anonymous"

        with self._lock:
            minute_bucket = self._minute_events.setdefault(safe_key, deque())
            hour_bucket = self._hour_events.setdefault(safe_key, deque())

            while minute_bucket and minute_bucket[0] <= minute_cutoff:
                minute_bucket.popleft()
            while hour_bucket and hour_bucket[0] <= hour_cutoff:
                hour_bucket.popleft()

            if len(minute_bucket) >= per_minute:
                retry_after = max(1, int(WINDOW_MINUTE_SECONDS - (now - minute_bucket[0])))
                return {"allowed": False, "scope": "minute", "retry_after_seconds": retry_after, "limit": per_minute}

            if len(hour_bucket) >= per_hour:
                retry_after = max(1, int(WINDOW_HOUR_SECONDS - (now - hour_bucket[0])))
                return {"allowed": False, "scope": "hour", "retry_after_seconds": retry_after, "limit": per_hour}

            minute_bucket.append(now)
            hour_bucket.append(now)
            return {"allowed": True, "scope": None, "retry_after_seconds": 0, "limit": None}


RATE_LIMITER = InMemoryAiRateLimiter()


def normalize_guard_text(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def is_osrs_market_question(message: str) -> bool:
    text = normalize_guard_text(message)
    if not text:
        return True
    if any(pattern in text for pattern in _BLOCK_PATTERNS):
        return False
    if any(keyword in text for keyword in _DOMAIN_KEYWORDS):
        return True
    return any(keyword in text for keyword in _ALLOWED_STRATEGY_HINTS)


def build_scope_guard_reply() -> dict[str, Any]:
    return {
        "summary": "I can help with OSRS flips, margins, risk, and slot decisions.",
        "actions": [
            {"item": "Ask about your flips", "decision": "review", "reason": "Try a question tied to items, margins, or time away."},
            {"item": "Stay in OSRS scope", "decision": "hold", "reason": "Homework, math, and general chat are outside this advisor."},
        ],
        "top_picks": [
            "What should I replace before 12 hours away?",
            "Which active trades look weakest right now?",
            "Where is my current setup taking the most risk?",
        ],
        "notes": [
            "Website preview stays focused on OSRS market guidance only.",
            "Plugin unlocks deeper slot-by-slot execution and exact trade handling.",
        ],
        "mode": "guard_scope",
        "plugin_cta": "Use the plugin for full execution once you are working inside an OSRS trade plan.",
    }


def build_rate_limit_reply(scope: str, retry_after_seconds: int) -> dict[str, Any]:
    scope_label = "this minute" if scope == "minute" else "this hour"
    return {
        "summary": "Let's slow down and refine one OSRS question at a time.",
        "actions": [
            {"item": "Current question", "decision": "review", "reason": "Pick the single highest-priority tradeoff first."},
            {"item": "Retry timing", "decision": "watch", "reason": f"Ask again in about {max(1, retry_after_seconds)} seconds."},
        ],
        "top_picks": [
            "What should I replace first?",
            "Which active trade is riskiest?",
            "What is safest to hold overnight?",
        ],
        "notes": [
            f"The website preview reached its pacing limit for {scope_label}.",
            "Fewer, more targeted questions keep replies faster and cheaper.",
        ],
        "mode": "guard_rate_limit",
        "plugin_cta": "Use the plugin for deeper execution once you know the exact trade you want to focus on.",
    }
