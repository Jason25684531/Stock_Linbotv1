"""C4 Web compatibility checks for the Strategy Platform seam."""

from types import SimpleNamespace

import pandas as pd


def _install_web_fixture(monkeypatch, *, resolve_error=None, empty=False):
    import app as app_module

    market = pd.DataFrame(
        [
            {
                "stock_id": "2330",
                "close_price": 952.0,
                "rsi": 59.0,
                "volume": 200000,
                "ma20": 910.0,
                "ma60": 870.0,
            }
        ]
    )
    persisted = pd.DataFrame() if empty else pd.DataFrame(
        [
            {
                "stock_id": "2330",
                "trade_date": "2026-04-10",
                "strategy": "v36_chip_momentum",
                "close_price": 950.0,
                "ai_score": 0.81,
                "rsi": 58.5,
                "volume": 180000,
                "news_boost_reason": None,
            }
        ]
    )
    calls = []

    class Runner:
        def resolve_spec(self, key):
            calls.append(key)
            if resolve_error is not None:
                raise resolve_error
            return SimpleNamespace(canonical_id="quality_platform")

    class Strategy:
        name = "v36_chip_momentum"
        display_name = "Chip Momentum"
        stop_loss = 0.08
        take_profit = 0.15

    class Manager:
        def get_strategy_runner(self):
            return Runner()

        def get_strategy(self, key):
            return Strategy() if key == "v36_chip_momentum" else None

        def get_active_strategy(self):
            return Strategy()

        def get_active_strategy_names(self):
            return ["v36_chip_momentum"]

    monkeypatch.setattr(app_module, "StrategyManager", Manager)
    monkeypatch.setattr(app_module, "_resolve_ui_baseline_date", lambda: "2026-04-10")
    monkeypatch.setattr(app_module, "_current_line_date", lambda: "2026-04-10")
    monkeypatch.setattr(app_module, "get_stock_data", lambda **kwargs: (market.copy(), "2026-04-10"))
    monkeypatch.setattr(app_module, "supplement_financial_data", lambda frame: frame)
    monkeypatch.setattr(app_module, "_get_stock_mentions_map", lambda _ids: {})
    monkeypatch.setattr(
        app_module,
        "_resolve_signal_news_info",
        lambda _row, _date, _mentions: {"raw": "", "items": [], "title": "", "is_bearish": False},
    )
    monkeypatch.setattr(
        app_module,
        "_load_strategy_candidates",
        lambda **kwargs: (
            persisted.copy(),
            {
                "requested_date": "2026-04-10",
                "recommendation_date": "2026-04-10",
                "market_anchor_date": "2026-04-10",
                "resolution_source": "heartbeat" if empty else "strategy_fallback",
                "fallback_used": empty,
                "has_persisted_snapshot": empty,
            },
            not empty,
        ),
    )
    return calls


def test_daily_signals_alias_uses_platform_resolution_and_preserves_payload(monkeypatch):
    from app import app as flask_app

    calls = _install_web_fixture(monkeypatch)
    response = flask_app.test_client().get("/api/daily-signals?strategy=v36&top_n=3")

    assert response.status_code == 200
    payload = response.get_json()
    assert calls == ["v36_chip_momentum"]
    assert payload["strategy_key"] == "v36_chip_momentum"
    assert payload["signals"][0]["stock_id"] == "2330"
    assert payload["signals"][0]["ai_score"] == 0.81


def test_daily_signals_platform_error_is_explicit_and_does_not_use_different_strategy(monkeypatch):
    from app import app as flask_app

    calls = _install_web_fixture(monkeypatch, resolve_error=RuntimeError("platform unavailable"))
    response = flask_app.test_client().get("/api/daily-signals?strategy=v36")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["error"] == "platform unavailable"
    assert payload["signals"] == []
    assert calls == ["v36_chip_momentum"]


def test_daily_signals_empty_snapshot_keeps_heartbeat_shape(monkeypatch):
    from app import app as flask_app

    calls = _install_web_fixture(monkeypatch, empty=True)
    response = flask_app.test_client().get("/api/daily-signals?strategy=v36")

    assert response.status_code == 200
    payload = response.get_json()
    assert calls == ["v36_chip_momentum"]
    assert payload["signals"] == []
    assert payload["resolution_source"] == "heartbeat"
    assert payload["strategy_key"] == "v36_chip_momentum"
