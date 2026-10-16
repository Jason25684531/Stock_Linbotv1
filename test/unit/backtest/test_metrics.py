import pytest

from core.backtest.metrics import calculate_metrics


def test_metrics_are_finite_or_explicitly_unavailable():
    metrics = calculate_metrics([100, 110, 99, 121], [{"profit_pct": 3}, {"profit_pct": -2}])
    assert metrics.get("cagr").value is not None
    assert metrics.get("profit_factor").value == 1.5


def test_zero_volatility_is_not_presented_as_zero_sharpe():
    metrics = calculate_metrics([100, 100, 100])
    assert metrics.get("sharpe").value is None
    assert metrics.get("sharpe").reason


@pytest.mark.parametrize('equity,mdd,duration,recovery', [
    ([100, 80, 120], -0.2, 1, 1),
    ([100, 120, 90, 120, 130], -0.25, 1, 1),
    ([100, 120, 90, 119, 121, 150], -0.25, 1, 2),
    ([100, 110, 120], 0, 0, 0),
    ([100, 100, 100], 0, 0, 0),
    ([100, 100, 80, 90], -0.2, 1, None),
])
def test_drawdown_durations_follow_the_deepest_episode(equity, mdd, duration, recovery):
    metrics = calculate_metrics(equity)
    assert metrics.get('max_drawdown').value == pytest.approx(mdd, rel=1e-9, abs=1e-12)
    assert metrics.get('drawdown_duration').value == duration
    assert metrics.get('recovery_duration').value == recovery
    assert bool(metrics.get('recovery_duration').reason) == (recovery is None)
