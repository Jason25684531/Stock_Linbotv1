"""Offline golden contracts for the existing Dashboard/LINE records paths."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.mcp_client import MCPClientError

GOLDEN = Path(__file__).parents[1] / "fixtures" / "baseline" / "cleanup_records_2026-10-14.json"
RECORDS = [
    {"open_price": 10, "close_price": 11, "volume": 3.9, "foreign_buy": 1.9, "trust_buy": -2.9, "dealer_buy": 0.9},
    {"open_price": 10, "close_price": 9, "volume": -1.9, "foreign_buy": 1.9, "trust_buy": 2.9, "dealer_buy": -0.9},
    {"open_price": 0, "close_price": 0, "volume": 0},
    {"open_price": None, "close_price": float("nan"), "volume": None},
    {},
]


def capture(monkeypatch, family, case):
    import app
    from app import dashboard_payloads as dashboard

    cache = dashboard._PostbackCache()
    monkeypatch.setattr(dashboard, "_postback_cache", cache)
    monkeypatch.setattr(app, "_postback_cache", cache)
    monkeypatch.setattr(app, "_current_line_date", lambda: "2026-10-14")
    monkeypatch.setattr(app, "_resolve_ui_baseline_date", lambda: "2026-10-14")
    calls = []

    def fetch(value):
        calls.append(value)
        if case == "mcp_error":
            raise MCPClientError("offline", endpoint="/test", correlation_id="cleanup")
        if case == "exception":
            raise ValueError("offline")
        if case == "none":
            return None
        return {"as_of_date": "2026-10-13", "records": [] if case == "empty" else RECORDS}

    client = SimpleNamespace(get_market_statistics_sync=fetch, get_foreign_investment_sync=fetch)
    monkeypatch.setattr(app, "get_mcp_client", lambda: client)
    snapshot = dashboard._build_market_snapshot if family == "market" else dashboard._build_chip_snapshot
    message = app._build_market_summary_messages if family == "market" else app._build_chip_trend_messages
    first = {"snapshot": snapshot(), "message": message()[0].text}
    if case == "cache":
        assert calls == ["2026-10-14", "2026-10-14"]
        assert {"snapshot": snapshot(), "message": message()[0].text} == first
        assert len(calls) == 2
    keys = sorted(cache._store)
    return {**first, "calls": calls, "cache_keys": keys}


@pytest.mark.parametrize("family", ["market", "chip"])
@pytest.mark.parametrize("case", ["records", "empty", "none", "mcp_error", "exception", "cache"])
def test_records_outputs_and_cache_match_precleanup_golden(monkeypatch, family, case):
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert capture(monkeypatch, family, case) == expected[f"{family}/{case}"]


def test_postback_cache_expiry_and_taipei_day_boundary(monkeypatch):
    from app import dashboard_payloads as dashboard

    cache = dashboard._PostbackCache()
    day = ["2026-10-14"]
    clock = [10.0]
    monkeypatch.setattr(cache, "_today_taipei", lambda: day[0])
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: clock[0])
    cache.set("market_summary", "before-midnight")
    assert cache.get("market_summary") == "before-midnight"
    day[0] = "2026-10-15"
    assert cache.get("market_summary") is None
    cache.set("market_summary", "new-day")
    clock[0] += cache._TTL_SECONDS + 1
    assert cache.get("market_summary") is None
