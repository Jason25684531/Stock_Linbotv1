import math

from core.research.normalize import parse_number


def test_parse_number_removes_thousands_separators():
    assert parse_number("1,673,263,794") == 1673263794.0


def test_parse_number_keeps_missing_source_values_as_nan():
    assert math.isnan(parse_number("--"))
