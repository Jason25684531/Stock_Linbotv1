"""Point-in-time, open-to-open research labels."""

import pandas as pd


DEFAULT_HORIZONS = (1, 5, 10, 20, 60)


def forward_return_definitions(horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> list[dict[str, object]]:
    return [
        {
            "label_id": f"forward_return_{horizon}d", "horizon": horizon, "entry_lag": 1,
            "entry_price_field": "adjusted_open", "exit_price_field": "adjusted_open",
            "price_basis": "local_adjusted", "formula_version": "1.0.0",
        }
        for horizon in horizons
    ]


def compute_forward_returns(quotes: pd.DataFrame, *, horizons: tuple[int, ...] = DEFAULT_HORIZONS) -> pd.DataFrame:
    """Add label values and reasons, aligned independently along each asset's rows."""

    result = quotes.sort_values(["stock_id", "trade_date"]).copy()
    result["entry_price"] = result.groupby("stock_id")["adjusted_open"].shift(-1)
    for horizon in horizons:
        label = f"forward_return_{horizon}d"
        reason = f"{label}_missing_reason"
        result[label] = float("nan")
        result[reason] = pd.NA
        for _, group in result.groupby("stock_id", sort=False):
            indices = group.index.tolist()
            opens = pd.to_numeric(group["adjusted_open"], errors="coerce").tolist()
            tradable = group.get("is_tradable_t1", pd.Series(True, index=group.index)).fillna(False).tolist()
            for position, index in enumerate(indices):
                if not tradable[position]:
                    result.loc[index, reason] = "t1_untradable"
                    continue
                entry, exit_ = position + 1, position + horizon + 1
                if exit_ >= len(indices):
                    result.loc[index, reason] = "tail_insufficient"
                    continue
                entry_open, exit_open = opens[entry], opens[exit_]
                if pd.notna(entry_open) and entry_open <= 0:
                    result.loc[index, reason] = "zero_denominator"
                elif pd.isna(entry_open) or pd.isna(exit_open):
                    result.loc[index, reason] = "adjusted_open_unavailable"
                else:
                    result.loc[index, label] = exit_open / entry_open - 1
    return result
