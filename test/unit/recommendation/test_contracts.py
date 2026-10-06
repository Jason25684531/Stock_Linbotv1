from __future__ import annotations

from datetime import date
import math

import pytest

from core.recommendation.contracts import Recommendation
from core.recommendation.normalizer import (
    normalize_legacy_row,
    serialize_recommendation,
    to_legacy_mapping,
)


def test_recommendation_is_immutable_and_serializes_dates() -> None:
    row = Recommendation(
        stock_id="2330",
        strategy_id="legacy_alias",
        asof_date=date(2026, 10, 5),
        score=None,
        rank=None,
        selected=None,
        target_weight=None,
        reason=None,
    )
    with pytest.raises(AttributeError):
        row.stock_id = "2317"  # type: ignore[misc]
    payload = serialize_recommendation(row)
    assert payload["asof_date"] == "2026-10-05"
    assert payload["score"] is None
    assert payload["rank"] is None
    assert payload["selected"] is None
    assert payload["target_weight"] is None
    assert payload["reason"] is None


def test_legacy_normalizer_does_not_invent_missing_semantics() -> None:
    row = normalize_legacy_row(
        {
            "stock_id": "2330",
            "trade_date": "2026-10-05",
            "strategy": "v31_hybrid",
            "close_price": 100.0,
            "rsi": 55.0,
            "volume": 123,
        }
    )
    assert row.strategy_id == "v31_hybrid"
    assert row.score is None
    assert row.rank is None
    assert row.selected is None
    assert row.target_weight is None
    assert row.reason is None
    assert to_legacy_mapping(row) == {
        "stock_id": "2330",
        "trade_date": "2026-10-05",
        "strategy": "v31_hybrid",
        "close_price": 100.0,
        "rsi": 55.0,
        "volume": 123,
    }


def test_legacy_score_maps_only_from_explicit_ai_score_and_numeric_parity() -> None:
    row = normalize_legacy_row(
        {
            "stock_id": "2330",
            "trade_date": "2026-10-05",
            "strategy": "v31_hybrid",
            "ai_score": 0.75,
        }
    )
    assert math.isclose(row.score, 0.75, rel_tol=1e-9, abs_tol=1e-12)
    assert to_legacy_mapping(row)["ai_score"] == 0.75
