from datetime import date, datetime

import pandas as pd

from core.research.normalize import apply_adjustments


def _quotes():
    return pd.DataFrame(
        {
            "trade_date": [date(2023, 1, 2), date(2023, 1, 3)],
            "stock_id": ["2330", "2330"],
            "raw_open": [100.0, 100.0],
            "raw_high": [110.0, 110.0],
            "raw_low": [90.0, 90.0],
            "raw_close": [100.0, 100.0],
        }
    )


def test_apply_adjustments_uses_only_events_strictly_after_each_trade_date():
    actions = pd.DataFrame(
        {"ex_date": [date(2023, 1, 3)], "stock_id": ["2330"], "event_factor": [0.9], "pre_ex_close": [100.0]}
    )

    result = apply_adjustments(_quotes(), actions, datetime(2026, 7, 31, 9))

    assert result.quotes["adjustment_factor"].tolist() == [0.9, 1.0]
    assert result.quotes["adjusted_close"].tolist() == [90.0, 100.0]
    assert result.quotes["raw_close"].tolist() == [100.0, 100.0]


def test_apply_adjustments_marks_prices_unavailable_without_an_official_source():
    result = apply_adjustments(_quotes(), None, datetime(2026, 7, 31, 9))

    assert result.quotes["adjustment_source"].eq("unavailable").all()
    assert result.quotes["adjusted_close"].isna().all()
    assert result.warnings == ["W007_adjustment_unavailable"]


def test_apply_adjustments_skips_non_positive_reference_denominators_without_infinity():
    actions = pd.DataFrame(
        {"ex_date": [date(2023, 1, 3)], "stock_id": ["2330"], "event_factor": [float("inf")], "pre_ex_close": [0.0]}
    )

    result = apply_adjustments(_quotes(), actions, datetime(2026, 7, 31, 9))

    assert result.quotes["adjustment_factor"].tolist() == [1.0, 1.0]
    assert not result.quotes.select_dtypes("number").isin([float("inf"), float("-inf")]).any().any()
    assert result.warnings == ["W007_adjustment_unavailable"]
