"""Canonical research market-data contracts and transformations."""

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


def validate_canonical_quotes(quotes: pd.DataFrame) -> None:
    """Reject incomplete canonical rows while permitting forward-compatible columns."""

    missing = set(CANONICAL_QUOTE_COLUMNS) - set(quotes.columns)
    if missing:
        raise SchemaError(f"F001_missing_required_column: {sorted(missing)}")
