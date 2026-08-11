import pandas as pd
import pytest

from core.research.factor_evaluation import EvaluationPolicy
from core.research.quantile_analysis import assign_quantiles, compute_quantile_returns


def test_assign_quantiles_uses_open_interval_floor_rule_without_qcut():
    data = pd.DataFrame({"rank_value": [0.01, 0.20, 0.21, 0.99], "member": [True, True, True, False]})

    result = assign_quantiles(data, rank_column="rank_value", policy=EvaluationPolicy(quantile_count=5))

    assert result["quantile"].tolist()[:3] == [1, 2, 2]
    assert pd.isna(result["quantile"].iloc[3])


def test_quantile_returns_produce_signed_aligned_spread_for_negative_factor():
    data = pd.DataFrame(
        {
            "factor_id": ["value"] * 5,
            "asof_date": ["2026-01-02"] * 5,
            "member": [True] * 5,
            "direction": [-1] * 5,
            "rank_value": [0.1, 0.3, 0.5, 0.7, 0.9],
            "direction_adjusted_rank": [0.9, 0.7, 0.5, 0.3, 0.1],
            "forward_return_1d": [0.05, 0.04, 0.03, 0.02, 0.01],
        }
    )

    result = compute_quantile_returns(data, horizons=(1,), policy=EvaluationPolicy(min_quantile_assets=5))

    summary = result["summary"].iloc[0]
    assert summary.raw_q5_minus_q1 == pytest.approx(-0.04)
    assert summary.aligned_long_short_spread == pytest.approx(0.04)
    assert summary.aligned_monotonicity == pytest.approx(1.0)


def test_perfect_positive_quantiles_are_ordered_and_thin_or_undirected_inputs_are_not_fabricated():
    data = pd.DataFrame(
        {
            "factor_id": ["momentum_20d"] * 5,
            "asof_date": ["2026-01-02"] * 5,
            "member": [True] * 5,
            "direction": [1] * 5,
            "rank_value": [0.1, 0.3, 0.5, 0.7, 0.9],
            "direction_adjusted_rank": [0.1, 0.3, 0.5, 0.7, 0.9],
            "forward_return_1d": [0.01, 0.02, 0.03, 0.04, 0.05],
        }
    )
    summary = compute_quantile_returns(data, horizons=(1,), policy=EvaluationPolicy(min_quantile_assets=5))["summary"].iloc[0]
    assert summary.raw_q5_minus_q1 > 0
    assert summary.raw_monotonicity == pytest.approx(1.0)
    assert summary.aligned_long_short_spread > 0

    thin = compute_quantile_returns(data.iloc[:4], horizons=(1,), policy=EvaluationPolicy(min_quantile_assets=5))
    assert thin["summary"].empty

    undirected = data.assign(factor_id="vwap_gap", direction=0, direction_adjusted_rank=float("nan"))
    undirected_summary = compute_quantile_returns(undirected, horizons=(1,), policy=EvaluationPolicy(min_quantile_assets=5))["summary"]
    assert "aligned_long_short_spread" not in undirected_summary.columns
