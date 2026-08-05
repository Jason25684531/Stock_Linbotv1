import math

import pandas as pd

from core.research.normalize import normalize_twse_closing_table, quote_lineage, parse_number, sort_canonical_quotes


def test_parse_number_removes_thousands_separators():
    assert parse_number("1,673,263,794") == 1673263794.0


def test_parse_number_keeps_missing_source_values_as_nan():
    assert math.isnan(parse_number("--"))


def test_quote_lineage_applies_one_source_to_the_entire_ohlc_row():
    lineage = quote_lineage("twse_rwd")

    assert lineage == {
        "raw_price_source": "twse_rwd",
        "is_fallback": False,
        "fallback_reason": "",
        "quality_status": "unverified",
    }
    assert not any(key.endswith(("open_source", "high_source", "low_source", "close_source")) for key in lineage)


def test_normalize_twse_closing_table_creates_twse_canonical_rows():
    table = {"data": [["2330", "100", "2", "3,000", "10", "12", "9", "11"]]}

    quotes = normalize_twse_closing_table(table, "2026-07-28", "retrieved")

    assert quotes.loc[0, ["stock_id", "market", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]].tolist() == ["2330", "TWSE", 10.0, 12.0, 9.0, 11.0, 100.0, 3000.0]


def test_normalize_sorts_by_stock_id_and_trade_date():
    unsorted = pd.DataFrame({
        "stock_id": ["2317", "2330", "2317"],
        "trade_date": ["2026-01-03", "2026-01-02", "2026-01-02"],
        "value": [1, 2, 3],
    })

    sorted_quotes = sort_canonical_quotes(unsorted)

    assert sorted_quotes[["stock_id", "trade_date"]].values.tolist() == [
        ["2317", "2026-01-02"], ["2317", "2026-01-03"], ["2330", "2026-01-02"],
    ]
