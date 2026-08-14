"""Small, research-only VectorBT boundary for D5."""

from __future__ import annotations

import pandas as pd


def run_vectorbt(close: pd.DataFrame, target_weights: pd.DataFrame, *, fee_rate: float, tax_rate: float) -> dict[str, object]:
    """Execute T+1 target weights with VectorBT; caller supplies execution-date prices."""
    import vectorbt as vbt

    close = close.sort_index().sort_index(axis=1).astype(float)
    weights = target_weights.pivot(index="execution_date", columns="asset_id", values="target_weight").reindex(index=close.index, columns=close.columns).ffill().fillna(0.0)
    # VectorBT order fees are symmetric here; use the mean of project buy/sell rates.
    portfolio = vbt.Portfolio.from_orders(close, size=weights, size_type="targetpercent", fees=fee_rate + tax_rate / 2, freq="1D", cash_sharing=True, group_by=True)
    return {"returns": portfolio.returns(), "value": portfolio.value(), "orders": portfolio.orders.records_readable, "stats": portfolio.stats()}
