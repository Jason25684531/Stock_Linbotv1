from core.backtest.metrics import calculate_metrics


def test_metrics_are_finite_or_explicitly_unavailable():
    metrics = calculate_metrics([100, 110, 99, 121], [{"profit_pct": 3}, {"profit_pct": -2}])
    assert metrics.get("cagr").value is not None
    assert metrics.get("profit_factor").value == 1.5


def test_zero_volatility_is_not_presented_as_zero_sharpe():
    metrics = calculate_metrics([100, 100, 100])
    assert metrics.get("sharpe").value is None
    assert metrics.get("sharpe").reason
