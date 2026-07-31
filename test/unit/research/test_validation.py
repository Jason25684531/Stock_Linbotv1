import inspect

import pandas as pd

from core.research.market_data import CANONICAL_QUOTE_COLUMNS
from core.research.validation import validate


def _quotes():
    quotes = pd.DataFrame({column: [None] for column in CANONICAL_QUOTE_COLUMNS})
    quotes["trade_date"] = ["2023-01-03"]
    quotes["stock_id"] = ["2330"]
    quotes[["raw_open", "raw_high", "raw_low", "raw_close"]] = [10.0, 12.0, 9.0, 11.0]
    quotes["volume"] = [1]
    quotes["amount"] = [100.0]
    return quotes


def test_contract_validation_reports_fixed_fatal_codes():
    quotes = _quotes().assign(raw_close=0.0, raw_low=-1.0)

    diagnostics = validate(quotes)

    assert [(item.code, item.severity, item.stage) for item in diagnostics] == [
        ("F004_nonpositive_price", "FATAL", "contract")
    ]


def test_contract_validation_detects_duplicate_keys_and_invalid_ohlc():
    quotes = pd.concat([_quotes(), _quotes()]).assign(raw_high=8.0)

    assert {item.code for item in validate(quotes)} == {"F002_duplicate_key", "F005_high_lt_low", "F006_ohlc_out_of_range"}


def test_contract_validation_emits_nonfatal_data_anomaly_warnings_without_rolling():
    quotes = _quotes().assign(volume=0, liquidity_basis="close_times_volume_proxy", adjustment_source="unavailable", is_fallback=True)

    diagnostics = validate(quotes)

    assert {(item.code, item.severity) for item in diagnostics} == {
        ("W002_zero_volume", "WARN"),
        ("W006_liquidity_proxy", "WARN"),
        ("W007_adjustment_unavailable", "WARN"),
        ("W008_fallback_used", "WARN"),
    }


def test_validation_exposes_no_runtime_severity_override():
    assert tuple(inspect.signature(validate).parameters) == ("quotes",)
