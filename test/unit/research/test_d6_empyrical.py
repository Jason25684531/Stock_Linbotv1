import numpy as np
import pandas as pd
import pytest

from core.backtest.metrics import calculate_metrics
from core.research.portfolio_validation import empyrical_crosscheck


def _returns_and_metrics():
    rng = np.random.default_rng(7)
    daily_returns = pd.Series(rng.normal(0.0004, 0.01, 300), index=pd.date_range("2023-01-01", periods=300))
    equity = (1 + daily_returns).cumprod() * 1_000_000.0
    equity_curve = [1_000_000.0] + equity.tolist()
    metrics = calculate_metrics(equity_curve, [])
    return daily_returns, metrics


def test_same_series_same_convention_metrics_match():
    daily_returns, metrics = _returns_and_metrics()
    result = empyrical_crosscheck("cfg", daily_returns, metrics)
    max_drawdown_row = result.loc[result["metric"] == "max_drawdown"].iloc[0]
    assert max_drawdown_row["status"] == "MATCH"


def test_convention_differing_metrics_are_labeled():
    daily_returns, metrics = _returns_and_metrics()
    result = empyrical_crosscheck("cfg", daily_returns, metrics)
    assert result["status"].isin(["MATCH", "CONVENTION_DIFFERENCE"]).all()
    sharpe_row = result.loc[result["metric"] == "sharpe_ratio"].iloc[0]
    assert sharpe_row["status"] in {"MATCH", "CONVENTION_DIFFERENCE"}


def test_unavailable_custom_metric_is_recorded_not_imputed():
    flat_returns = pd.Series([0.0] * 10, index=pd.date_range("2023-01-01", periods=10))
    metrics = calculate_metrics([100.0] * 11, [])  # zero volatility -> sharpe unavailable
    result = empyrical_crosscheck("cfg", flat_returns, metrics)
    sharpe_row = result.loc[result["metric"] == "sharpe_ratio"].iloc[0]
    assert sharpe_row["status"] == "CUSTOM_METRIC_UNAVAILABLE"
    assert pd.isna(sharpe_row["custom_engine_value"])


def test_deterministic_output():
    daily_returns, metrics = _returns_and_metrics()
    first = empyrical_crosscheck("cfg", daily_returns, metrics)
    second = empyrical_crosscheck("cfg", daily_returns, metrics)
    pd.testing.assert_frame_equal(first, second)


def test_empyrical_unavailable_and_zero_denominator_have_reasons(monkeypatch):
    import core.research.portfolio_validation as validation

    returns = pd.Series([0.0] * 10)
    metrics = calculate_metrics([100.0] * 11)
    result = empyrical_crosscheck('cfg', returns, metrics)
    mdd = result.set_index('metric').loc['max_drawdown']
    assert mdd['absolute_difference'] == 0 and pd.isna(mdd['relative_difference'])
    assert mdd['relative_difference_reason'] == 'zero_denominator'
    assert 'Infinity' not in result.to_json()

    monkeypatch.setattr(validation, '_empyrical_value', lambda *args: float('inf'))
    result = empyrical_crosscheck('cfg', returns, metrics)
    assert result['empyrical_value'].isna().all()
    assert result['empyrical_value_reason'].eq('non_finite_value').all()
    assert result.set_index('metric').loc['max_drawdown', 'status'] == 'EMPYRICAL_METRIC_UNAVAILABLE'
