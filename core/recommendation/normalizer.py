"""Explicit compatibility mappings for recommendation boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import Recommendation, RecommendationRow


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def normalize_legacy_row(
    row: Mapping[str, Any], *, default_strategy_id: str | None = None
) -> RecommendationRow:
    """Map legacy names while keeping missing semantic fields as ``None``."""

    score = _value(row, "score", "ai_score")
    strategy_id = _value(row, "strategy_id", "strategy")
    if strategy_id is None:
        strategy_id = default_strategy_id
    return Recommendation(
        stock_id=_value(row, "stock_id"),
        strategy_id=strategy_id,
        asof_date=_value(row, "asof_date", "trade_date"),
        score=score,
        rank=_value(row, "rank"),
        selected=_value(row, "selected"),
        target_weight=_value(row, "target_weight"),
        reason=_value(row, "reason"),
        close_price=_value(row, "close_price"),
        ai_score=_value(row, "ai_score"),
        rsi=_value(row, "rsi"),
        volume=_value(row, "volume"),
        news_boost_reason=_value(row, "news_boost_reason"),
    )


def to_legacy_mapping(
    row: RecommendationRow, *, include_none: bool = False
) -> dict[str, Any]:
    """Map canonical fields to existing legacy persistence names only."""

    payload: dict[str, Any] = {
        "stock_id": row.stock_id,
        "trade_date": row.asof_date.isoformat()
        if hasattr(row.asof_date, "isoformat")
        else row.asof_date,
        "strategy": row.strategy_id,
        "close_price": row.close_price,
        "ai_score": row.ai_score if row.ai_score is not None else row.score,
        "rsi": row.rsi,
        "volume": row.volume,
        "news_boost_reason": row.news_boost_reason,
    }
    if include_none:
        return payload
    return {key: value for key, value in payload.items() if value is not None}


def serialize_recommendation(
    row: RecommendationRow, *, include_none: bool = True
) -> dict[str, Any]:
    return row.to_dict(include_none=include_none)


__all__ = [
    "normalize_legacy_row",
    "serialize_recommendation",
    "to_legacy_mapping",
]
