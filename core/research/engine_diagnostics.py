"""Read-only D6 engine-comparison diagnostics for frozen D5/D6 evidence."""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd


RETURN_METRICS = ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar")
FROZEN_WHITELIST = {"metric_convention", "daily_retarget", "full_rebuild", "cost_model", "integer_rounding", "cash_handling", "fill_constraints"}


def normalized_metrics(returns: pd.Series, periods_per_year: int = 252) -> dict[str, float | None]:
    """Calculate comparison metrics from a raw daily-return source only."""
    values = pd.to_numeric(returns, errors="coerce").dropna().astype(float)
    if values.empty:
        return {metric: None for metric in RETURN_METRICS}
    total = float((1 + values).prod() - 1)
    annualized = float((1 + total) ** (periods_per_year / len(values)) - 1)
    volatility = float(values.std(ddof=0) * math.sqrt(periods_per_year))
    downside = values[values < 0].std(ddof=0)
    cumulative = (1 + values).cumprod()
    drawdown = float((cumulative / cumulative.cummax() - 1).min())
    sharpe = float(values.mean() / values.std(ddof=0) * math.sqrt(periods_per_year)) if values.std(ddof=0) else None
    sortino = float(values.mean() / downside * math.sqrt(periods_per_year)) if downside and not np.isnan(downside) else None
    return {"total_return": total, "annualized_return": annualized, "annualized_volatility": volatility, "sharpe": sharpe, "sortino": sortino, "max_drawdown": drawdown, "calmar": annualized / abs(drawdown) if drawdown else None}


def rebalance_semantics(config_id: str, orders: pd.DataFrame, rebalance_dates: pd.DatetimeIndex) -> dict:
    """Prove semantics from VBT order records rather than target-weight inference."""
    date_column = next((column for column in ("Timestamp", "timestamp", "Date", "date") if column in orders), None)
    if date_column is None:
        raise ValueError("VectorBT order records have no timestamp column")
    dates = pd.to_datetime(orders[date_column]).dt.normalize()
    allowed = pd.DatetimeIndex(rebalance_dates).normalize()
    non_rebalance = ~dates.isin(allowed)
    price = pd.to_numeric(orders.get("Price", 0.0), errors="coerce").fillna(0.0)
    size = pd.to_numeric(orders.get("Size", 0.0), errors="coerce").fillna(0.0)
    non_rebalance_orders = int(non_rebalance.sum())
    return {"config_id": config_id, "target_rebalance_date_count": len(allowed.unique()), "vectorbt_dates_with_orders": int(dates.nunique()), "vectorbt_order_count": int(len(orders)), "orders_on_non_rebalance_dates": non_rebalance_orders, "notional_traded_on_non_rebalance_dates": float((size[non_rebalance].abs() * price[non_rebalance].abs()).sum()), "semantics_status": "POTENTIAL_DAY2_EXECUTION_SEMANTICS_DEFECT" if non_rebalance_orders else "ORDERS_ONLY_ON_REBALANCE_DATES"}


def attribution_status(*, original_gap: float, residual_gap: float, contributions: Mapping[str, float]) -> dict:
    """Report marginal counterfactual effects; contributions are deliberately non-additive."""
    unknown = set(contributions) - FROZEN_WHITELIST
    if unknown:
        raise ValueError(f"unknown frozen-whitelist reason: {sorted(unknown)}")
    explained = None if original_gap == 0 else 100 * (1 - abs(residual_gap) / abs(original_gap))
    status = "EXPLAINED_BY_FROZEN_WHITELIST" if explained is not None and explained >= 100 else ("PARTIALLY_EXPLAINED" if contributions else "UNEXPLAINED")
    return {"original_gap": original_gap, "residual_unexplained_gap": residual_gap, "explained_gap_pct": explained, "evidence_status": status, "final_attribution_status": status, "attribution_method": "marginal_counterfactual", **{f"{name}_marginal_effect": value for name, value in contributions.items()}}
