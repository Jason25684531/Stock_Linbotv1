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


def add_asset_id(quotes: pd.DataFrame) -> pd.DataFrame:
    """Add `asset_id` as a read-only mirror of `stock_id` for compatibility consumers."""

    return quotes.assign(asset_id=quotes["stock_id"])


def add_is_tradable(quotes: pd.DataFrame) -> pd.DataFrame:
    """Flag per-row tradability, independent of the research universe filter in universe.py."""

    degraded = quotes["quality_status"].isin(["degraded", "unverified"])
    return quotes.assign(is_tradable=(quotes["volume"] > 0) & ~degraded)


def add_available_at(quotes: pd.DataFrame) -> pd.DataFrame:
    """Add a conservative `available_at`: the observation instant of the next trading day.

    `market_closed_at` is this row's own observation instant (the instant the trade
    date's market activity was observed), not a claim about when the data became
    available. `available_at` never equals the row's own trade date; for the last
    trading day present in this dataset there is no known next trading day, so
    `available_at` is null rather than guessed from a calendar-day offset.
    """

    trade_dates = pd.Index(sorted(pd.to_datetime(quotes["trade_date"]).unique()))
    observation_time = (
        quotes.assign(trade_date=pd.to_datetime(quotes["trade_date"]), market_closed_at=pd.to_datetime(quotes["market_closed_at"]))
        .drop_duplicates("trade_date")
        .set_index("trade_date")["market_closed_at"]
    )
    next_trade_date = pd.Series(trade_dates, index=trade_dates).shift(-1)
    available_at_by_date = next_trade_date.map(observation_time)
    return quotes.assign(available_at=pd.to_datetime(quotes["trade_date"]).map(available_at_by_date))


def to_wide(quotes: pd.DataFrame, columns: list[str]) -> dict[str, pd.DataFrame]:
    """Create aligned computation frames after proving the natural key is unique."""

    if quotes.duplicated(["trade_date", "stock_id"]).any():
        raise SchemaError("F002_duplicate_key: duplicate (trade_date, stock_id)")
    index = pd.Index(sorted(quotes["trade_date"].unique()), name="trade_date")
    stock_ids = pd.Index(sorted(quotes["stock_id"].unique()), name="stock_id")
    frames = {
        column: quotes.pivot(index="trade_date", columns="stock_id", values=column).reindex(index=index, columns=stock_ids)
        for column in columns
    }
    if any(not frame.index.equals(index) or not frame.columns.equals(stock_ids) for frame in frames.values()):
        raise SchemaError("F008_wide_frame_misalignment")
    return frames
