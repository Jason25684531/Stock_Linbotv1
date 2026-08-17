import pandas as pd
import pytest

from core.research.portfolio_validation import (
    check_structural_parity,
    compare_engines,
    overall_engine_status,
)


def _write_frozen(tmp_path, rows):
    path = tmp_path / "config.csv"
    pd.DataFrame(rows, columns=["asof_date", "execution_date", "asset_id", "target_weight"]).to_csv(path, index=False)
    return path


def test_structural_parity_passes_for_identical_data(tmp_path):
    rows = [("2023-01-02", "2023-01-03", "A", 0.5), ("2023-01-02", "2023-01-03", "B", 0.5)]
    frozen_path = _write_frozen(tmp_path, rows)
    replay_targets = pd.DataFrame(rows, columns=["asof_date", "execution_date", "asset_id", "target_weight"])
    evidence = check_structural_parity(replay_targets, frozen_path)
    assert evidence.iloc[0]["target_intent_parity_status"] == "PASS"


def test_structural_parity_fails_when_replay_diverges(tmp_path):
    frozen_path = _write_frozen(tmp_path, [("2023-01-02", "2023-01-03", "A", 0.5)])
    mutated = pd.DataFrame([("2023-01-02", "2023-01-03", "A", 0.9)], columns=["asof_date", "execution_date", "asset_id", "target_weight"])
    with pytest.raises(ValueError, match="structural parity failed"):
        check_structural_parity(mutated, frozen_path)


def test_structural_parity_rejects_same_close_frozen_artifact(tmp_path):
    frozen_path = _write_frozen(tmp_path, [("2023-01-03", "2023-01-03", "A", 1.0)])
    replay_targets = pd.DataFrame([("2023-01-03", "2023-01-03", "A", 1.0)], columns=["asof_date", "execution_date", "asset_id", "target_weight"])
    with pytest.raises(ValueError, match="same-close"):
        check_structural_parity(replay_targets, frozen_path)


def _returns(values):
    return pd.Series(values, index=pd.date_range("2023-01-01", periods=len(values)))


def test_return_metrics_within_tolerance_match():
    vbt = {m: 1.0 for m in ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar")}
    custom = {**vbt, "sharpe": 1.02}  # 2% relative difference, within 10% tolerance
    for metric in ("turnover", "trade_count", "estimated_cost", "ending_value"):
        vbt[metric] = custom[metric] = 100.0
    comparison = compare_engines("cfg", vbt, custom, _returns([0.01, 0.02, -0.01]), _returns([0.01, 0.02, -0.01]))
    row = comparison.loc[comparison["metric"] == "sharpe"].iloc[0]
    assert row["comparison_status"] == "MATCH_WITHIN_TOLERANCE"


def test_return_metric_beyond_tolerance_is_unexplained():
    vbt = {m: 1.0 for m in ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar")}
    custom = {**vbt, "sharpe": 2.0}  # 100% relative difference, well beyond tolerance
    for metric in ("turnover", "trade_count", "estimated_cost", "ending_value"):
        vbt[metric] = custom[metric] = 100.0
    comparison = compare_engines("cfg", vbt, custom, _returns([0.01, 0.02, -0.01]), _returns([0.01, 0.02, -0.01]))
    row = comparison.loc[comparison["metric"] == "sharpe"].iloc[0]
    assert row["comparison_status"] == "UNEXPLAINED_DIFFERENCE"
    assert overall_engine_status(comparison) == "FAIL"


def test_cost_metrics_are_expected_model_difference():
    vbt = {m: 1.0 for m in ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar")}
    custom = dict(vbt)
    vbt.update({"turnover": 100.0, "trade_count": 10, "estimated_cost": 50.0, "ending_value": 1_000_000.0})
    custom.update({"turnover": 120.0, "trade_count": 12, "estimated_cost": 70.0, "ending_value": 999_000.0})
    comparison = compare_engines("cfg", vbt, custom, _returns([0.01, 0.02, -0.01]), _returns([0.01, 0.02, -0.01]))
    cost_rows = comparison.loc[comparison["metric"].isin(["turnover", "trade_count", "estimated_cost", "ending_value"])]
    assert (cost_rows["comparison_status"] == "EXPECTED_MODEL_DIFFERENCE").all()
    assert overall_engine_status(comparison) == "PASS_WITH_EXPLAINED_DIFFERENCES"


def test_identical_metrics_still_flag_expected_cost_model_difference():
    # Cost/activity metrics are *unconditionally* EXPECTED_MODEL_DIFFERENCE per
    # design.md decision B, even when the numbers happen to match exactly -
    # VectorBT's approximation is declared different by construction, so a bare
    # "PASS" (no cost-model differences at all) is not expected to occur.
    metrics = {m: 1.0 for m in ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar", "turnover", "trade_count", "estimated_cost", "ending_value")}
    identical_returns = _returns([0.01, 0.02, -0.01, 0.005, 0.0, 0.03])
    comparison = compare_engines("cfg", metrics, dict(metrics), identical_returns, identical_returns)
    assert overall_engine_status(comparison) == "PASS_WITH_EXPLAINED_DIFFERENCES"
