from core.validation.bootstrap import bootstrap_metrics
from core.validation.correlation import correlation_analysis
from core.validation.cost_sensitivity import cost_sensitivity
from core.validation.split import split_is_oos
from core.validation.walk_forward import walk_forward_folds


def test_split_and_walk_forward_do_not_overlap():
    train, test = split_is_oos([1, 2, 3, 4])
    assert train == [1, 2] and test == [3, 4]
    assert all(fold["train_end"] < fold["test_start"] for fold in walk_forward_folds(12, 4, 2))


def test_bootstrap_seed_is_repeatable():
    result = bootstrap_metrics([.01, -.01], 10, seed=7, method="block")
    assert result == bootstrap_metrics([.01, -.01], 10, seed=7, method="block")
    assert {"cagr", "sharpe", "sortino", "max_drawdown"} <= result.distributions.keys()


def test_fixed_and_multi_segment_splits_are_isolated():
    values, dates = [1, 2, 3, 4], ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert split_is_oos(values, dates=dates, split_date="2026-01-03") == ([1, 2], [3, 4])
    assert split_is_oos(values, dates=dates, segments=[("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04")]) == [([1, 2], [3, 4])]


def test_cost_and_correlation_outputs_are_complete():
    costs = {"fee": [.001], "tax": [.003], "slippage": [0], "minimum_fee": [20], "fill_delay": [0]}
    assert len(cost_sensitivity(costs, lambda value: {"cagr": 0, "sharpe": 0, "sortino": 0, "max_drawdown": 0, "turnover": 0})) == 1
    import pandas as pd
    result = correlation_analysis(pd.DataFrame({"a": [.01, -.01], "b": [.02, -.02]}), holdings={"a": {"1"}, "b": {"1", "2"}}, exposures={"a": {"tech": 1}, "b": {"tech": .5}}, drawdowns=pd.DataFrame({"a": [0, -.1], "b": [0, -.2]}))
    assert {"daily_return_correlation", "holding_overlap", "exposure_correlation", "drawdown_correlation"} == result.keys()
