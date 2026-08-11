import pandas as pd
import pytest

from core.research.factor_evaluation import EvaluationPolicy, compute_top_n_retention


def test_top_n_turnover_is_equal_weight_not_one_minus_retention_when_pool_sizes_change():
    data = pd.DataFrame(
        {
            "factor_id": ["value"] * 5,
            "direction": [1] * 5,
            "asof_date": ["2026-01-02"] * 2 + ["2026-01-03"] * 3,
            "asset_id": ["A", "B", "A", "B", "C"],
            "member": [True] * 5,
            "rank_value": [0.9, 0.8, 0.9, 0.8, 0.7],
            "direction_adjusted_rank": [0.9, 0.8, 0.9, 0.8, 0.7],
        }
    )

    result = compute_top_n_retention(data, policy=EvaluationPolicy(top_n=3))

    row = result.iloc[0]
    assert row.effective_n == 2
    assert row.top_n_retention == pytest.approx(1.0)
    assert row.equal_weight_turnover == pytest.approx(1 / 3)


def test_top_n_retention_and_turnover_have_exact_same_and_disjoint_bounds():
    same = pd.DataFrame(
        {
            "factor_id": ["value"] * 4,
            "direction": [1] * 4,
            "asof_date": ["2026-01-02"] * 2 + ["2026-01-03"] * 2,
            "asset_id": ["A", "B", "A", "B"],
            "member": [True] * 4,
            "rank_value": [0.9, 0.8, 0.9, 0.8],
            "direction_adjusted_rank": [0.9, 0.8, 0.9, 0.8],
        }
    )
    same_row = compute_top_n_retention(same, policy=EvaluationPolicy(top_n=2)).iloc[0]
    assert same_row.top_n_retention == pytest.approx(1.0)
    assert same_row.equal_weight_turnover == pytest.approx(0.0)

    disjoint = same.assign(asset_id=["A", "B", "C", "D"])
    disjoint_row = compute_top_n_retention(disjoint, policy=EvaluationPolicy(top_n=2)).iloc[0]
    assert disjoint_row.top_n_retention == pytest.approx(0.0)
    assert disjoint_row.equal_weight_turnover == pytest.approx(1.0)
