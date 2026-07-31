"""Daily TWSE ordinary-share universe selection."""

import re

import pandas as pd


def build_mask(quotes: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Build a date-by-security membership mask from point-in-time quote facts."""

    work = quotes.sort_values(["stock_id", "trade_date"]).copy()
    missing_amount = work["amount"].isna()
    work["liquidity"] = work["amount"].where(~missing_amount, work["raw_close"] * work["volume"])
    work["mean_liquidity"] = work.groupby("stock_id")["liquidity"].transform(lambda values: values.rolling(20, min_periods=1).mean())
    work["member"] = (
        work["stock_id"].astype(str).str.fullmatch(r"[1-9]\d{3}")
        & work["market"].eq("TWSE")
        & work["raw_close"].ge(10)
        & work["mean_liquidity"].ge(20_000_000)
        & work["volume"].gt(0)
    )
    index = pd.Index(sorted(work["trade_date"].unique()), name="trade_date")
    columns = pd.Index(sorted(work["stock_id"].unique()), name="stock_id")
    mask = work.pivot(index="trade_date", columns="stock_id", values="member").reindex(index=index, columns=columns, fill_value=False)
    return mask.astype(bool), ["W006_liquidity_proxy"] if missing_amount.any() else []


def universe_counts(mask: pd.DataFrame, liquidity_basis: pd.DataFrame) -> pd.DataFrame:
    """Summarize daily membership and the liquidity source used by members."""

    return pd.DataFrame(
        {
            "trade_date": mask.index,
            "count": mask.sum(axis=1).astype(int),
            "liquidity_basis_official": (mask & liquidity_basis.eq("official_amount")).sum(axis=1).astype(int),
            "liquidity_basis_proxy": (mask & liquidity_basis.eq("close_times_volume_proxy")).sum(axis=1).astype(int),
        }
    )
