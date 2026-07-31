import math

from core.research.normalize import quote_lineage, parse_number


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
