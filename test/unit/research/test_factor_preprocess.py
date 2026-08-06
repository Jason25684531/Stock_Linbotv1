import pandas as pd
import pytest

from core.research.factor_preprocess import preprocess_factors


def test_preprocesses_only_member_cross_sections_with_midranks_and_directions():
    factors = pd.DataFrame(
        {
            "asof_date": ["2025-01-02"] * 4 + ["2025-01-03"] * 3,
            "asset_id": ["A", "B", "C", "X", "A", "B", "C"],
            "factor_id": ["value"] * 4 + ["value"] * 3,
            "raw_value": [1.0, 2.0, 3.0, 1_000_000.0, 10.0, 20.0, None],
        }
    )
    membership = pd.DataFrame(
        {
            "trade_date": ["2025-01-02"] * 4 + ["2025-01-03"] * 3,
            "stock_id": ["A", "B", "C", "X", "A", "B", "C"],
            "member": [True, True, True, False, True, True, True],
        }
    )

    result = preprocess_factors(factors, membership, {"value": -1})

    first_day = result.loc[result["asof_date"].eq(pd.Timestamp("2025-01-02"))]
    assert first_day.loc[first_day.asset_id.eq("A"), "rank_value"].item() == 1 / 6
    assert first_day.loc[first_day.asset_id.eq("C"), "direction_adjusted_rank"].item() == pytest.approx(1 / 6)
    assert first_day.loc[first_day.asset_id.eq("X"), "winsorized_value"].isna().item()
    assert result.loc[result.asset_id.eq("C") & result.asof_date.eq(pd.Timestamp("2025-01-03")), "rank_value"].isna().item()
    pd.testing.assert_series_equal(result["raw_value"], factors["raw_value"], check_names=False)


def test_undetermined_and_tiny_cross_sections_do_not_fabricate_scores():
    factors = pd.DataFrame({"asof_date": ["2025-01-02", "2025-01-02"], "asset_id": ["A", "B"], "factor_id": ["unknown", "single"], "raw_value": [1.0, 2.0]})
    membership = pd.DataFrame({"trade_date": ["2025-01-02", "2025-01-02"], "stock_id": ["A", "B"], "member": [True, False]})

    result = preprocess_factors(factors, membership, {"unknown": 0, "single": 1})

    assert result.loc[result.factor_id.eq("unknown"), "direction_adjusted_rank"].isna().all()
    assert result.loc[result.factor_id.eq("single"), "rank_value"].isna().all()
