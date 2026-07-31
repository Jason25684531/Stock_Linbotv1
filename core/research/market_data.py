"""Canonical research market-data contracts and transformations."""

from dataclasses import dataclass

import pandas as pd


CANONICAL_QUOTE_COLUMNS = (
    "trade_date", "stock_id", "market", "currency",
    "raw_open", "raw_high", "raw_low", "raw_close",
    "adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close",
    "adjustment_factor", "adjustment_source", "adjustment_as_of",
    "volume", "amount", "transaction_count", "liquidity_basis",
    "raw_price_source", "is_fallback", "fallback_reason", "quality_status",
    "market_closed_at", "retrieved_at", "ingested_at", "source_revision", "quality_flags",
)


class SchemaError(ValueError):
    code = "F001_missing_required_column"


@dataclass(frozen=True)
class DataWindow:
    requested_start: pd.Timestamp
    requested_end: pd.Timestamp
    loaded_start: pd.Timestamp
    loaded_end: pd.Timestamp
    maximum_lookback: int


def validate_canonical_quotes(quotes: pd.DataFrame) -> None:
    """Reject incomplete canonical rows while permitting forward-compatible columns."""

    missing = set(CANONICAL_QUOTE_COLUMNS) - set(quotes.columns)
    if missing:
        raise SchemaError(f"F001_missing_required_column: {sorted(missing)}")


def loaded_window(
    requested_start: object, requested_end: object, *, maximum_lookback: int, safety_days: int = 10
) -> DataWindow:
    """Load enough business days for the longest factor plus a safety margin."""

    requested_start = pd.Timestamp(requested_start)
    requested_end = pd.Timestamp(requested_end)
    return DataWindow(
        requested_start,
        requested_end,
        requested_start - pd.offsets.BDay(maximum_lookback + safety_days),
        requested_end,
        maximum_lookback,
    )


def restrict_to_requested_window(quotes: pd.DataFrame, window: DataWindow) -> pd.DataFrame:
    """Exclude warm-up rows from published output."""

    dates = pd.to_datetime(quotes["trade_date"])
    return quotes.loc[(dates >= window.requested_start) & (dates <= window.requested_end)].copy()
