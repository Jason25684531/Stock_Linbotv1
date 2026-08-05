from pathlib import Path

import pandas as pd
import pytest

from core.research.market_data import (
    CANONICAL_QUOTE_COLUMNS,
    SchemaError,
    add_asset_id,
    add_available_at,
    add_is_tradable,
    loaded_window,
    restrict_to_requested_window,
    to_wide,
    validate_canonical_quotes,
)


def _quotes():
    return pd.DataFrame({column: [None] for column in CANONICAL_QUOTE_COLUMNS})


def test_canonical_quote_schema_rejects_missing_required_columns_and_tolerates_extras():
    quotes = _quotes().assign(extra_column="allowed")

    validate_canonical_quotes(quotes)
    with pytest.raises(SchemaError, match="F001"):
        validate_canonical_quotes(quotes.drop(columns="raw_close"))


def test_canonical_quote_schema_carries_provenance_and_time_fields_only():
    source = Path(validate_canonical_quotes.__code__.co_filename).read_text(encoding="utf-8")

    assert {"market_closed_at", "retrieved_at", "adjustment_as_of", "liquidity_basis", "quality_status"} <= set(CANONICAL_QUOTE_COLUMNS)
    assert "cash_dividend" not in source
    assert "stock_split_ratio" not in source
    # available_at is now a derived compatibility column (see add_available_at below);
    # it is not part of the required canonical schema itself.
    assert "available_at" not in CANONICAL_QUOTE_COLUMNS


def test_asset_id_mirrors_stock_id():
    quotes = pd.DataFrame({"stock_id": ["2330", "2317"]})

    result = add_asset_id(quotes)

    assert result["asset_id"].tolist() == result["stock_id"].tolist()


def test_is_tradable_rules():
    quotes = pd.DataFrame({
        "volume": [0.0, 10.0, 10.0, 10.0],
        "quality_status": ["ok", "degraded", "unverified", "ok"],
    })

    result = add_is_tradable(quotes)

    assert result["is_tradable"].tolist() == [False, False, False, True]


def test_available_at_uses_next_trading_day_not_market_close():
    quotes = pd.DataFrame({
        "trade_date": ["2026-01-02", "2026-01-05", "2026-01-06"],
        "market_closed_at": ["2026-01-02T13:30", "2026-01-05T13:30", "2026-01-06T13:30"],
    })

    result = add_available_at(quotes)

    assert result["available_at"].iloc[0] == pd.Timestamp("2026-01-05T13:30")
    assert result["available_at"].iloc[1] == pd.Timestamp("2026-01-06T13:30")
    # available_at is never the row's own trade date/observation instant
    assert result["available_at"].iloc[0] != pd.Timestamp(quotes["market_closed_at"].iloc[0])


def test_available_at_is_null_for_last_loaded_trade_date():
    quotes = pd.DataFrame({
        "trade_date": ["2026-01-02", "2026-01-05"],
        "market_closed_at": ["2026-01-02T13:30", "2026-01-05T13:30"],
    })

    result = add_available_at(quotes)

    assert pd.isna(result["available_at"].iloc[-1])


def test_legacy_and_canonical_columns_coexist():
    quotes = pd.DataFrame({"stock_id": ["2330"], "trade_date": ["2026-01-02"]})

    result = add_asset_id(quotes)

    assert {"stock_id", "asset_id", "trade_date"} <= set(result.columns)


def test_loaded_window_includes_lookback_and_only_emits_requested_dates():
    requested_start = pd.Timestamp("2024-01-02")
    window = loaded_window(requested_start, pd.Timestamp("2024-12-31"), maximum_lookback=253)
    quotes = pd.DataFrame({"trade_date": [window.loaded_start, requested_start, pd.Timestamp("2024-01-03")]})

    assert (requested_start - window.loaded_start).days >= 263
    assert restrict_to_requested_window(quotes, window).trade_date.tolist() == [requested_start, pd.Timestamp("2024-01-03")]


def test_to_wide_rejects_duplicate_keys_and_aligns_every_frame():
    quotes = pd.DataFrame(
        {
            "trade_date": ["2023-01-02", "2023-01-02", "2023-01-03", "2023-01-03"],
            "stock_id": ["2330", "2317", "2330", "2317"],
            "raw_close": [1.0, 2.0, 3.0, 4.0],
            "volume": [10.0, 20.0, 30.0, 40.0],
        }
    )

    wide = to_wide(quotes, ["raw_close", "volume"])

    assert wide["raw_close"].index.equals(wide["volume"].index)
    assert wide["raw_close"].columns.equals(wide["volume"].columns)
    with pytest.raises(SchemaError, match="F002"):
        to_wide(pd.concat([quotes, quotes.iloc[[0]]]), ["raw_close"])
