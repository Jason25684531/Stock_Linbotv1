import pandas as pd
import pytest

from core.backtest.costs import CostModel
from core.research.portfolio_validation import COST_SCENARIOS, assess_strict_oos_feasibility, run_cost_sensitivity, temporal_stability


def test_temporal_split_is_chronological_and_deterministic():
    returns = pd.Series(range(100), index=pd.date_range("2023-01-01", periods=100), dtype=float) / 1000
    result = temporal_stability("cfg", returns)
    assert list(result["segment"]) == ["early_60pct", "middle_20pct", "late_20pct"]
    assert result.loc[result["segment"] == "early_60pct", "observations"].iloc[0] == 60
    assert result.loc[result["segment"] == "middle_20pct", "observations"].iloc[0] == 20
    assert result.loc[result["segment"] == "late_20pct", "observations"].iloc[0] == 20
    assert (result["label"] == "temporal_stability_not_strict_oos").all()


def test_temporal_split_never_shuffles():
    returns = pd.Series([0.01, -0.02, 0.03, 0.04, -0.01], index=pd.date_range("2023-01-01", periods=5))
    result = temporal_stability("cfg", returns)
    assert result.loc[result["segment"] == "early_60pct", "observations"].iloc[0] == 3


def test_cost_scenarios_are_frozen_constants():
    assert COST_SCENARIOS == {"BASE": 0.0, "STRESS": 0.001, "PESSIMISTIC": 0.002}


def test_cost_sensitivity_runs_all_scenarios_and_preserves_tax():
    cost_model = CostModel(0.001425, 0.003, 20)
    targets = pd.DataFrame([
        ("2023-01-02", "2023-01-03", "A", "cfg", 1.0),
    ], columns=["asof_date", "execution_date", "asset_id", "config_id", "target_weight"])
    prices = pd.DataFrame({"A": [100.0, 105.0, 110.0]}, index=pd.to_datetime(["2023-01-03", "2023-01-10", "2023-01-17"]))

    result = run_cost_sensitivity("cfg", targets, prices, cost_model, initial_capital=1_000_000.0)

    assert list(result["scenario"]) == ["BASE", "STRESS", "PESSIMISTIC"]
    assert len(result) == 3
    # higher slippage scenarios must not silently modify the statutory tax rate
    assert cost_model.tax_rate == 0.003


def test_strict_oos_infeasible_when_composite_weights_are_full_sample_scalars(tmp_path):
    path = tmp_path / "candidate_factors.csv"
    pd.DataFrame([{"factor_id": "momentum_20d", "mean_ic": 0.05, "icir": 0.3}]).to_csv(path, index=False)
    feasible, reason = assess_strict_oos_feasibility(path)
    assert feasible is False
    assert "D4" in reason


def test_strict_oos_feasible_when_no_full_sample_scalars(tmp_path):
    path = tmp_path / "candidate_factors.csv"
    pd.DataFrame([{"factor_id": "momentum_20d"}]).to_csv(path, index=False)
    feasible, _ = assess_strict_oos_feasibility(path)
    assert feasible is True
