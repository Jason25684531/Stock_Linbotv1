import pandas as pd

from core.research.engine_diagnostics import (
    attribution_status,
    normalized_metrics,
    rebalance_semantics,
)


def test_normalized_metrics_use_the_same_return_formula():
    returns = pd.Series([0.01, -0.005, 0.02], index=pd.date_range("2024-01-01", periods=3))
    assert normalized_metrics(returns) == normalized_metrics(returns.copy())


def test_rebalance_semantics_counts_non_rebalance_orders():
    orders = pd.DataFrame({"Timestamp": pd.to_datetime(["2024-01-02", "2024-01-03"]), "Size": [2, 3], "Price": [10.0, 11.0]})
    row = rebalance_semantics("cfg", orders, pd.DatetimeIndex(["2024-01-02"]))
    assert row["orders_on_non_rebalance_dates"] == 1
    assert row["semantics_status"] == "POTENTIAL_DAY2_EXECUTION_SEMANTICS_DEFECT"


def test_attribution_is_marginal_not_an_additive_claim():
    row = attribution_status(original_gap=10.0, residual_gap=2.0, contributions={"daily_retarget": 7.0})
    assert row["explained_gap_pct"] == 80.0
    assert row["attribution_method"] == "marginal_counterfactual"
