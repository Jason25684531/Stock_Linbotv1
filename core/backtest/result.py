"""Typed, dependency-free values exchanged by backtest, validation and charts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MetricValue:
    value: float | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is not None and self.reason is not None:
            raise ValueError("a calculated metric must not carry an unavailable reason")
        if self.value is None and not self.reason:
            raise ValueError("an unavailable metric requires a reason")


@dataclass
class TradeRecord:
    stock_id: str
    side: str = "sell"
    quantity: int = 0
    buy_date: str | None = None
    sell_date: str | None = None
    buy_price: float | None = None
    sell_price: float | None = None
    profit_pct: float | None = None
    reason: str | None = None
    strategy: str | None = None


@dataclass
class PositionSnapshot:
    stock_id: str
    quantity: int
    cost: float
    opened_at: str
    market_price: float | None = None


@dataclass
class DrawdownRecord:
    start: str | None = None
    trough: str | None = None
    recovery: str | None = None
    value: float = 0.0


@dataclass
class PerformanceMetrics:
    values: dict[str, MetricValue] = field(default_factory=dict)

    def get(self, name: str) -> MetricValue:
        return self.values.get(name, MetricValue(None, "metric is not available"))


@dataclass
class RollingMetrics:
    window: int
    values: list[dict[str, MetricValue]] = field(default_factory=list)


@dataclass
class BootstrapResult:
    seed: int | None
    confidence_intervals: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    distributions: dict[str, list[float]] = field(default_factory=dict)
    loss_probability: dict[str, float] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    folds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParameterSurface:
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BacktestResult:
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    metrics: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    positions: list[PositionSnapshot] = field(default_factory=list)
    drawdowns: list[DrawdownRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
