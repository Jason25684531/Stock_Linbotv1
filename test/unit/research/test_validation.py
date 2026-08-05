import inspect

import pandas as pd

from core.research.market_data import CANONICAL_QUOTE_COLUMNS
from core.research import validation
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


def test_validation_performs_no_rolling_or_factor_stage_diagnostics():
    source = open(validation.__file__, encoding="utf-8").read()

    assert ".rolling(" not in source
    assert "W012" not in source


def test_blank_stock_id_is_fatal():
    quotes = _quotes().assign(stock_id="")

    assert {item.code for item in validate(quotes)} == {"F012_blank_stock_id"}


def test_null_stock_id_is_fatal():
    quotes = _quotes().assign(stock_id=None)

    assert {item.code for item in validate(quotes)} == {"F013_null_stock_id"}


def test_populated_stock_id_triggers_neither_blank_nor_null_code():
    diagnostics = validate(_quotes())

    assert not {"F012_blank_stock_id", "F013_null_stock_id"} & {item.code for item in diagnostics}


def test_infinite_required_value_is_fatal():
    quotes = _quotes().assign(amount=float("inf"))

    assert {item.code for item in validate(quotes)} == {"F014_infinite_value"}


def test_finite_values_do_not_trigger_the_infinite_value_code():
    diagnostics = validate(_quotes())

    assert "F014_infinite_value" not in {item.code for item in diagnostics}


def test_unsorted_dates_only_warns_and_does_not_mutate():
    quotes = pd.concat([_quotes().assign(trade_date="2023-01-05"), _quotes().assign(trade_date="2023-01-03")], ignore_index=True)
    before = quotes.copy(deep=True)

    diagnostics = validate(quotes)

    assert "W014_unsorted_dates" in {item.code for item in diagnostics}
    pd.testing.assert_frame_equal(quotes, before)


def test_sorted_dates_do_not_trigger_the_unsorted_warning():
    quotes = pd.concat([_quotes().assign(trade_date="2023-01-03"), _quotes().assign(trade_date="2023-01-05")], ignore_index=True)

    assert "W014_unsorted_dates" not in {item.code for item in validate(quotes)}


def test_missing_close_column_message_names_the_common_alias():
    diagnostics = validate(_quotes().drop(columns="raw_close"))

    assert "raw_close (aka close)" in diagnostics[0].detail
