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
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    calendar = pd.DatetimeIndex(sorted(result["trade_date"].unique()))
    next_dates = pd.Series(calendar, index=calendar).shift(-1)
    rows = result.set_index(["trade_date", "stock_id"])
    result["entry_price"] = result.groupby("stock_id")["adjusted_open"].shift(-1)
    for horizon in horizons:
        label = f"forward_return_{horizon}d"
        reason = f"{label}_missing_reason"
        result[label] = float("nan")
        result[reason] = pd.NA
        for _, group in result.groupby("stock_id", sort=False):
            indices = group.index.tolist()
            for position, index in enumerate(indices):
                trade_date, stock_id = result.loc[index, ["trade_date", "stock_id"]]
                entry_date = next_dates.get(trade_date)
                exit_position = calendar.get_loc(trade_date) + horizon + 1
                entry = rows.loc[(entry_date, stock_id)] if entry_date is not pd.NaT and (entry_date, stock_id) in rows.index else None
                exit_date = calendar[exit_position] if exit_position < len(calendar) else None
                exit_ = rows.loc[(exit_date, stock_id)] if exit_date is not None and (exit_date, stock_id) in rows.index else None
                configured_tradable = result.loc[index, "is_tradable_t1"] if "is_tradable_t1" in result else True
                if entry is None or not bool(configured_tradable):
                    result.loc[index, reason] = "t1_untradable"
                    continue
                if exit_ is None:
                    result.loc[index, reason] = "tail_insufficient"
                    continue
                entry_open, exit_open = pd.to_numeric(entry["adjusted_open"], errors="coerce"), pd.to_numeric(exit_["adjusted_open"], errors="coerce")
                if pd.notna(entry_open) and entry_open <= 0:
                    result.loc[index, reason] = "zero_denominator"
                elif pd.isna(entry_open) or pd.isna(exit_open):
                    result.loc[index, reason] = "adjusted_open_unavailable"
                else:
                    result.loc[index, label] = exit_open / entry_open - 1
    return result
