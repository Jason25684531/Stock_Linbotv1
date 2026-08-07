"""Canonical, long-form research dataset assembly."""

from collections.abc import Mapping

import numpy as np
import pandas as pd


def build_research_dataset(
    processed: pd.DataFrame, membership: pd.DataFrame, labels: pd.DataFrame, quotes: pd.DataFrame, *, run_id: str
) -> pd.DataFrame:
    """Combine factor values, membership, timing, and labels into one stable table."""

    member_fields = membership.rename(columns={"trade_date": "asof_date", "stock_id": "asset_id"}).copy()
    member_fields["asof_date"] = pd.to_datetime(member_fields["asof_date"])
    extras = [column for column in member_fields if column not in {"asof_date", "asset_id", "member"}]
    dataset = processed.merge(member_fields.loc[:, ["asof_date", "asset_id", *extras]], on=["asof_date", "asset_id"], how="left")
    label_columns = [column for column in labels if column.startswith("forward_return_")] + ["trade_date", "stock_id", "entry_price"]
    dataset = dataset.merge(
        labels.loc[:, label_columns].rename(columns={"trade_date": "asof_date", "stock_id": "asset_id"}),
        on=["asof_date", "asset_id"], how="left",
    )
    calendar = pd.Series(sorted(pd.to_datetime(quotes["trade_date"]).unique()))
    next_dates = pd.Series(calendar.shift(-1).to_numpy(), index=calendar)
    dataset["factor_asof_date"] = dataset["asof_date"]
    dataset["source_max_trade_date"] = dataset["asof_date"]
    dataset["execution_date"] = dataset["asof_date"].map(next_dates)
    for column in ("factor_asof_time", "source_max_available_at", "execution_time", "entry_price_time"):
        dataset[column] = pd.NaT
    dataset["run_id"] = run_id
    dataset["factor_missing_reason"] = pd.NA
    missing_factor = dataset["raw_value"].isna()
    reason_checks = (
        (dataset.get("listing_date", pd.Series(pd.NaT, index=dataset.index)).isna(), "listing_date_unavailable"),
        (~dataset.get("listing_history_sufficient", pd.Series(False, index=dataset.index)).fillna(False), "listing_history_insufficient"),
        (~dataset["member"], dataset.get("exclusion_reason", pd.Series("not_in_universe", index=dataset.index)).fillna("not_in_universe")),
        (pd.Series(True, index=dataset.index), "insufficient_lookback"),
    )
    for matches, reason in reason_checks:
        unassigned = missing_factor & dataset["factor_missing_reason"].isna() & matches
        dataset.loc[unassigned, "factor_missing_reason"] = reason if isinstance(reason, str) else reason.loc[unassigned]
    reason_columns = [column for column in dataset if column.endswith("_missing_reason") and column.startswith("forward_return_")]
    dataset["label_missing_reason"] = dataset[reason_columns].bfill(axis=1).iloc[:, 0] if reason_columns else pd.NA
    numeric = dataset.select_dtypes(include="number").columns
    dataset.loc[:, numeric] = dataset.loc[:, numeric].replace([np.inf, -np.inf], np.nan)
    return dataset
