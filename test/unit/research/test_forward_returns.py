import numpy as np
import pandas as pd
import pytest

from core.research.forward_returns import compute_forward_returns


def test_forward_returns_are_open_to_open_per_asset_with_next_day_entry():
    quotes = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2025-01-01", periods=8).tolist() * 2,
            "stock_id": ["A"] * 8 + ["B"] * 8,
            "adjusted_open": [99, 100, 102, 104, 106, 108, 110, 114] + [10, 10, 10, 10, 10, 10, 10, 10],
        }
    )

    result = compute_forward_returns(quotes, horizons=(1, 5))

    first = result.loc[result.stock_id.eq("A")].iloc[0]
    assert first.forward_return_1d == pytest.approx(0.02)
    assert first.forward_return_5d == pytest.approx(0.10)
    assert result.loc[result.stock_id.eq("B"), "forward_return_1d"].iloc[0] == 0


def test_forward_returns_have_reasoned_nulls_without_substitution_or_infinity():
    quotes = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2025-01-01", periods=4),
            "stock_id": "A",
            "adjusted_open": [10, 0, np.nan, 14],
            "is_tradable_t1": [True, False, True, False],
        }
    )

    result = compute_forward_returns(quotes, horizons=(1,))

    assert pd.isna(result.loc[0, "forward_return_1d"])
    assert result.loc[0, "forward_return_1d_missing_reason"] == "zero_denominator"
    assert result.loc[1, "forward_return_1d_missing_reason"] == "t1_untradable"
    assert result.loc[3, "forward_return_1d_missing_reason"] == "t1_untradable"
    assert not np.isinf(result.select_dtypes("number").to_numpy()).any()
