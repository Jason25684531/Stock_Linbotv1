"""Small, research-only VectorBT boundary for D5."""

from __future__ import annotations

import pandas as pd


def run_vectorbt(close: pd.DataFrame, target_weights: pd.DataFrame, *, fee_rate: float, tax_rate: float, initial_capital: float = 1_000_000.0, sparse_rebalance: bool) -> dict[str, object]:
    """Execute T+1 target weights with VectorBT; caller supplies execution-date prices."""
    import vectorbt as vbt

    close = close.sort_index().sort_index(axis=1).astype(float)
    targets = target_weights.pivot(index="execution_date", columns="asset_id", values="target_weight").reindex(columns=close.columns).fillna(0.0)
    scheduled_dates = pd.DatetimeIndex(targets.index)
    weights = targets.reindex(close.index).ffill().fillna(0.0)
    if sparse_rebalance:
        # NaN means no target-percent instruction, so holdings drift until the next target.
        weights = targets.reindex(close.index)
    # VectorBT order fees are symmetric here; use the mean of project buy/sell rates.
    portfolio = vbt.Portfolio.from_orders(close, size=weights, size_type="targetpercent", fees=fee_rate + tax_rate / 2, init_cash=initial_capital, freq="1D", cash_sharing=True, group_by=True)
    orders = portfolio.orders.records_readable
    actual_order_dates = pd.DatetimeIndex(pd.to_datetime(orders["Timestamp"]).unique()).sort_values() if len(orders) else pd.DatetimeIndex([])
    return {"returns": portfolio.returns(), "value": portfolio.value(), "orders": orders, "stats": portfolio.stats(), "instruction_matrix": weights, "scheduled_instruction_dates": scheduled_dates, "actual_order_dates": actual_order_dates, "orders_on_non_rebalance_dates": int((~pd.to_datetime(orders["Timestamp"]).isin(scheduled_dates)).sum())}
