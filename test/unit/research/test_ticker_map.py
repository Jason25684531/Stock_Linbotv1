"""Tests for the runtime ticker mapping resource."""

from datetime import date
from pathlib import Path

from core.research.ticker_map import TickerMapping, resource_path, resolve


def test_resolve_returns_twse_symbol_inside_validity_window():
    mapping = resolve("2330", date(2024, 1, 2))

    assert mapping is not None
    assert mapping.yahoo_symbol == "2330.TW"
    assert mapping.market == "TWSE"


def test_resolve_returns_none_outside_validity_window():
    assert resolve("2330", date(1899, 12, 31)) is None


def test_runtime_mapping_resource_is_not_a_test_fixture():
    path = resource_path()

    assert path.name == "ticker_map.csv"
    assert "core" in path.parts and "research" in path.parts and "resources" in path.parts
    assert "test" not in path.parts


def test_tpex_can_be_represented_without_an_adapter():
    mapping = TickerMapping("1234", "TPEx", "1234", "1234.TWO", None, None, "active")

    assert mapping.market == "TPEx"


def test_only_ticker_mapping_source_contains_vendor_suffixes():
    research_root = Path(__file__).resolve().parents[3] / "core" / "research"
    for source_file in research_root.rglob("*.py"):
        if source_file.name == "ticker_map.py":
            continue
        source = source_file.read_text(encoding="utf-8")
        assert ".TW" not in source
        assert ".TWO" not in source
