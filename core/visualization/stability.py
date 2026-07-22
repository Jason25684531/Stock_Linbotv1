"""Plotly views over completed result objects; this module never computes signals."""
from __future__ import annotations

import plotly.graph_objects as go

from core.backtest.result import (BacktestResult, BootstrapResult, MetricValue,
                                  ParameterSurface, RollingMetrics, WalkForwardResult)


def _figure(title, x=(), y=(), name="value"):
    figure = go.Figure(go.Scatter(x=x, y=y, mode="lines", name=name))
    figure.update_layout(title=title, template="plotly_white")
    return figure


def _metric_value(value: MetricValue):
    if value.value is None:
        return None, value.reason
    return value.value, None


def equity_chart(result: BacktestResult, is_oos_start=None):
    dates, equity = zip(*result.equity_curve) if result.equity_curve else ((), ())
    figure = _figure("Equity curve", dates, equity, "equity")
    if is_oos_start is not None:
        figure.add_vline(x=is_oos_start, line_dash="dash", annotation_text="OOS")
    return figure


def rolling_chart(result: RollingMetrics, metric="sharpe"):
    values, reasons = [], []
    for index, row in enumerate(result.values):
        value, reason = _metric_value(row.get(metric, MetricValue(None, "metric is unavailable")))
        values.append(value)
        if reason:
            reasons.append(f"{index}: {reason}")
    figure = _figure(f"Rolling {metric}", list(range(len(values))), values, metric)
    if reasons:
        figure.add_annotation(text="<br>".join(reasons), showarrow=False, xref="paper", yref="paper", x=0, y=1)
    return figure


def walk_forward_chart(result: WalkForwardResult):
    figure = go.Figure()
    for fold in result.folds:
        figure.add_trace(go.Bar(name=f"fold {fold['fold']}", x=["train", "test"],
                                 y=[fold["train_end"] - fold["train_start"], fold["test_end"] - fold["test_start"]]))
    figure.update_layout(barmode="group", title="Walk-forward folds", template="plotly_white")
    return figure


def bootstrap_chart(result: BootstrapResult):
    names = list(result.confidence_intervals)
    lows = [result.confidence_intervals[name][0] for name in names]
    highs = [result.confidence_intervals[name][1] for name in names]
    figure = go.Figure(go.Bar(x=names, y=[high - low for low, high in zip(lows, highs)], base=lows, name="95% CI"))
    figure.update_layout(title="Bootstrap confidence intervals", template="plotly_white")
    return figure


def heatmap(result: ParameterSurface, title="Parameter surface"):
    rows = result.rows
    figure = go.Figure(go.Heatmap(z=[list(row.values()) for row in rows]))
    figure.update_layout(title=title, template="plotly_white")
    return figure


def cost_sensitivity_chart(result: ParameterSurface):
    rows = result.rows
    x = list(range(len(rows)))
    figure = _figure("Cost sensitivity", x, [row.get("cagr") for row in rows], "CAGR")
    for metric in ("sharpe", "sortino", "max_drawdown", "turnover"):
        figure.add_trace(go.Scatter(x=x, y=[row.get(metric) for row in rows], mode="lines", name=metric))
    return figure


def correlation_chart(result: ParameterSurface, title="Strategy return correlation"):
    return heatmap(result, title)


def holding_overlap_chart(result: ParameterSurface):
    return heatmap(result, "Holding overlap")


def comparison_chart(without_risk: BacktestResult, with_risk: BacktestResult):
    dates, equity = zip(*without_risk.equity_curve) if without_risk.equity_curve else ((), ())
    figure = _figure("Risk-control comparison", dates, equity, "without risk control")
    risk_dates, risk_equity = zip(*with_risk.equity_curve) if with_risk.equity_curve else ((), ())
    figure.add_trace(go.Scatter(x=risk_dates, y=risk_equity, mode="lines", name="with risk control"))
    return figure


def underwater_chart(result: BacktestResult):
    dates, equity = zip(*result.equity_curve) if result.equity_curve else ((), ())
    peak, drawdowns = 0.0, []
    for value in equity:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1 if peak else None)
    return _figure("Drawdown underwater", dates, drawdowns, "drawdown")


def drawdown_period_chart(result: BacktestResult):
    return go.Figure(go.Bar(
        x=[record.start for record in result.drawdowns],
        y=[record.value for record in result.drawdowns], name="drawdown"),
        layout={"title": "Drawdown periods", "template": "plotly_white"})
