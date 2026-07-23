from core.backtest.result import BacktestResult, BootstrapResult, DrawdownRecord, MetricValue, ParameterSurface, RollingMetrics
from core.visualization import bootstrap_chart, cost_sensitivity_chart, drawdown_period_chart, equity_chart, rolling_chart


def test_charts_are_constructed_without_engine_imports():
    result = BacktestResult(equity_curve=[("2026-01-01", 100)])
    assert len(equity_chart(result).data) == 1
    assert len(bootstrap_chart(BootstrapResult(7, {"sharpe": (-1, 1)})).data) == 1
    figure = rolling_chart(RollingMetrics(2, [{"sharpe": MetricValue(None, "insufficient samples")}]))
    assert "insufficient samples" in figure.layout.annotations[0].text
    assert len(cost_sensitivity_chart(ParameterSurface([{"cagr": 0, "sharpe": 0, "sortino": 0, "max_drawdown": 0, "turnover": 0}])).data) == 5
    assert len(drawdown_period_chart(BacktestResult(drawdowns=[DrawdownRecord(start="2026-01-01", value=-.1)])).data) == 1
