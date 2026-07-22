"""Backtest domain primitives; the CLI remains a compatibility boundary."""

from .engine import BacktestEngine, PortfolioBacktestEngine
from .metrics import MetricValue, calculate_metrics
from .result import BacktestResult, PerformanceMetrics

__all__ = ["BacktestEngine", "BacktestResult", "MetricValue", "PerformanceMetrics", "PortfolioBacktestEngine", "calculate_metrics"]
