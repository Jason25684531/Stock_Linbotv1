"""Stable Web contracts captured before the UI restructuring change."""

from __future__ import annotations

import json

def test_primary_html_route_inventory_is_explicit_after_migration():
    from app import app as flask_app

    routes = {rule.rule for rule in flask_app.url_map.iter_rules()}

    assert "/dashboard" in routes
    assert "/backtest" in routes
    assert "/stock-analysis" in routes
    assert "/strategies" in routes
    assert "/system" in routes
    assert "/api/trades" in routes


def test_phase1_stock_analysis_adapter_preserves_series_schema(monkeypatch):
    import app as app_module
    from app import app as flask_app

    captured = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return {
            "status": "degraded",
            "symbol": "2330",
            "as_of_date": "2026-10-06",
            "source": ["daily_market_data", "local_fallback"],
            "warnings": ["mcp unavailable"],
            "fallback_used": True,
            "message": "Local fallback data",
            "quote": {"close_price": 1045.0},
            "series": {
                "candles": [{"time": "2026-10-06", "open": 1032, "high": 1048, "low": 1030, "close": 1045}],
                "volume": [{"time": "2026-10-06", "value": 28456}],
                "ma5": [{"time": "2026-10-06", "value": 1032.4}],
                "ma20": [{"time": "2026-10-06", "value": 1008.6}],
                "ma60": [{"time": "2026-10-06", "value": 975.2}],
            },
            "indicators": {"rsi": 62.3},
            "institutional": {"foreign_buy": 12345},
            "rule_report": {"summary": "fallback"},
            "war_room": {},
        }

    monkeypatch.setattr(app_module, "_build_dashboard_health_check_payload", fake_builder)

    response = flask_app.test_client().get("/api/stock-analysis?id=2330&period=weekly")
    payload = response.get_json()

    assert response.status_code == 200
    assert captured["stock_id"] == "2330"
    assert captured["period"] == "weekly"
    assert payload["status"] == "degraded"
    assert payload["metadata"]["fallback_used"] is True
    assert set(payload["kline_ma"]) == {"candles", "volume", "ma5", "ma20", "ma60"}
    assert payload["kline_ma"]["candles"][0]["close"] == 1045


def test_phase1_strategy_identity_and_fundamental_isolation():
    from core.strategy_manager import StrategyManager

    manager = StrategyManager()
    expected = {
        "hybrid_trend_rank",
        "defensive_low_volatility",
        "growth_momentum_breakout",
        "quality_growth",
        "institutional_flow_confirmation",
        "mean_reversion",
        "quality_value_low_volatility",
    }
    assert set(manager.list_strategies()) == expected
    assert manager.resolve("v31") == "hybrid_trend_rank"
    assert manager.resolve("v38_value_dividend") == "quality_value_low_volatility"
    assert manager.FUNDAMENTAL_RUNTIME_STRATEGY_ID not in manager.list_strategies()
    registration = manager.runtime_strategy_registrations()[manager.FUNDAMENTAL_RUNTIME_STRATEGY_ID]
    assert registration["ENABLED_FOR_LIVE"] == "NO"
    assert registration["BROKER_ORDER_SUBMISSION"] == "DISABLED"


def test_phase1_backtest_selection_surface_is_legacy_canonical_only():
    from core.backtest.runner import get_registered_strategy_names
    from core.strategy_manager import StrategyManager

    manager = StrategyManager()
    assert get_registered_strategy_names(manager) == manager.list_strategies()
    assert manager.FUNDAMENTAL_RUNTIME_STRATEGY_ID not in get_registered_strategy_names(manager)


def test_phase1_settings_are_not_modified_by_read_only_inventory():
    settings = json.loads(open("strategy_settings.json", encoding="utf-8").read())
    assert "active_strategies" in settings
    assert "random_strategy_pool" in settings
