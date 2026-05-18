from __future__ import annotations

from typing import Any

from app.services.board_optimizer import build_board_plan


def build_recommendation_board(settings: dict[str, Any] | None = None, candidate_cache: Any | None = None) -> dict[str, Any]:
    """Compatibility wrapper for the board-first recommender.

    The old recommender module is intentionally replaced with the new board optimizer
    entry point so future callers do not revive item-list-first behavior.
    """
    return build_board_plan(settings=settings, candidate_cache=candidate_cache)
