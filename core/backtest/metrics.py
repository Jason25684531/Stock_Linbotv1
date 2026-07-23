"""Risk metrics with explicit unavailable values instead of misleading zero/inf."""
from __future__ import annotations

import math
from statistics import fmean, pstdev

from .result import MetricValue, PerformanceMetrics

TRADING_DAYS = 252


def _na(reason: str) -> MetricValue:
    return MetricValue(None, reason)


def _value(number: float) -> MetricValue:
    return MetricValue(float(number))


def _returns(equity: list[float]) -> list[float]:
    return [(current / previous) - 1 for previous, current in zip(equity, equity[1:]) if previous > 0]


def max_drawdown(equity: list[float]) -> tuple[MetricValue, int, int | None]:
    if not equity:
        return _na("equity curve is empty"), 0, None
    peak, peak_index, worst, trough = equity[0], 0, 0.0, 0
    for index, value in enumerate(equity):
        if value > peak:
            peak, peak_index = value, index
        drawdown = (value / peak) - 1 if peak else 0.0
        if drawdown < worst:
            worst, trough = drawdown, index
    recovery = next((index for index in range(trough + 1, len(equity)) if equity[index] >= peak), None)
    return _value(worst), trough - peak_index, recovery


def calculate_metrics(equity: list[float], trades: list[dict] | None = None,
                      periods_per_year: int = TRADING_DAYS, risk_free_rate: float = 0.0) -> PerformanceMetrics:
    """Return the 18 defined metrics; all ratios distinguish unavailable from zero."""
    trades = trades or []
    result: dict[str, MetricValue] = {}
    returns = _returns(equity)
    periods = len(returns)
    if len(equity) < 2 or equity[0] <= 0:
        reason = "at least two positive equity observations are required"
        return PerformanceMetrics({name: _na(reason) for name in METRIC_NAMES})
    total_return = equity[-1] / equity[0] - 1
    result["total_return"] = _value(total_return)
    result["cagr"] = _value((equity[-1] / equity[0]) ** (periods_per_year / periods) - 1)
    result["annualized_return"] = _value(fmean(returns) * periods_per_year) if returns else _na("no returns")
    volatility = pstdev(returns) * math.sqrt(periods_per_year) if len(returns) > 1 else 0.0
    result["annualized_volatility"] = _value(volatility) if len(returns) > 1 else _na("at least two returns are required")
    downside = [min(0.0, value) for value in returns]
    downside_dev = math.sqrt(fmean([value * value for value in downside])) * math.sqrt(periods_per_year) if downside else 0.0
    result["downside_deviation"] = _value(downside_dev) if downside else _na("no returns")
    annual_excess = fmean(returns) * periods_per_year - risk_free_rate if returns else 0.0
    result["sharpe"] = _value(annual_excess / volatility) if volatility else _na("return volatility is zero")
    result["sortino"] = _value(annual_excess / downside_dev) if downside_dev else _na("downside deviation is zero")
    mdd, dd_duration, recovery = max_drawdown(equity)
    result["max_drawdown"] = mdd
    result["drawdown_duration"] = _value(dd_duration)
    result["recovery_duration"] = _value((recovery - dd_duration) if recovery is not None else 0) if recovery is not None else _na("drawdown has not recovered")
    result["calmar"] = _value(result["cagr"].value / abs(mdd.value)) if mdd.value and result["cagr"].value is not None else _na("maximum drawdown is zero")
    profits = [float(item.get("profit_pct", item.get("profit", 0))) for item in trades]
    wins, losses = [x for x in profits if x > 0], [-x for x in profits if x < 0]
    result["win_rate"] = _value(len(wins) / len(profits)) if profits else _na("no closed trades")
    result["profit_factor"] = _value(sum(wins) / sum(losses)) if losses else _na("no losing trades")
    result["payoff_ratio"] = _value(fmean(wins) / fmean(losses)) if wins and losses else _na("both winning and losing trades are required")
    result["max_consecutive_losses"] = _value(_max_consecutive_losses(profits)) if profits else _na("no closed trades")
    result["exposure"] = _value(sum(bool(item.get("exposed", True)) for item in trades) / max(1, len(trades)))
    result["turnover"] = _value(sum(abs(float(item.get("notional", 0))) for item in trades) / equity[0])
    result["trade_count"] = _value(len(trades))
    holding_days = [float(item.get("days", item.get("hold_days", 0))) for item in trades]
    result["avg_hold_days"] = _value(fmean(holding_days)) if holding_days else _na("no closed trades")
    return PerformanceMetrics(result)


def _max_consecutive_losses(profits: list[float]) -> int:
    streak = best = 0
    for profit in profits:
        streak = streak + 1 if profit < 0 else 0
        best = max(best, streak)
    return best


METRIC_NAMES = ("total_return", "cagr", "annualized_return", "annualized_volatility", "downside_deviation", "sharpe", "sortino", "max_drawdown", "drawdown_duration", "recovery_duration", "calmar", "win_rate", "profit_factor", "payoff_ratio", "max_consecutive_losses", "exposure", "turnover", "trade_count", "avg_hold_days")
