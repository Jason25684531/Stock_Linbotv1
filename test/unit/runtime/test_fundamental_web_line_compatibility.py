from __future__ import annotations

import pandas as pd

from core.runtime.fundamental_production import STRATEGY_ID


def _patch_shared_reader(monkeypatch, app_module):
    market = pd.DataFrame([{"stock_id": "2330", "close_price": 700.0, "volume": 1000}])
    monkeypatch.setattr(app_module, "get_stock_data", lambda *args, **kwargs: (market.copy(), "2026-09-17"))
    monkeypatch.setattr(app_module, "supplement_financial_data", lambda frame: frame)
    monkeypatch.setattr(app_module, "_current_line_date", lambda: "2026-09-17")
    monkeypatch.setattr(app_module, "_resolve_ui_baseline_date", lambda: "2026-09-17")
    monkeypatch.setattr(app_module, "format_market_fallback_notice", lambda meta, name: "")


def test_web_blocked_fundamental_is_explicit_empty_state(monkeypatch):
    from app import app as flask_app
    import app as app_module

    _patch_shared_reader(monkeypatch, app_module)
    monkeypatch.setattr(
        app_module,
        "_load_strategy_candidates",
        lambda **kwargs: (pd.DataFrame(), {"recommendation_date": "2026-09-17", "has_persisted_snapshot": False}, False),
    )

    response = flask_app.test_client().get(f"/api/daily-signals?strategy={STRATEGY_ID}")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["strategy_key"] == STRATEGY_ID
    assert payload["signals"] == []
    assert payload["has_persisted_snapshot"] is False


def test_line_blocked_fundamental_does_not_fallback_to_legacy(monkeypatch):
    import app as app_module

    _patch_shared_reader(monkeypatch, app_module)
    monkeypatch.setattr(
        app_module,
        "_load_strategy_candidates",
        lambda **kwargs: (pd.DataFrame(), {"recommendation_date": "2026-09-17", "has_persisted_snapshot": False}, False),
    )

    reply = app_module.get_strategy_recommendation(strategy_key=STRATEGY_ID)
    assert isinstance(reply, str)
    assert STRATEGY_ID not in reply
    assert "G2/G3" in reply


def test_web_eligible_fundamental_uses_shared_reader(monkeypatch):
    from app import app as flask_app
    import app as app_module

    _patch_shared_reader(monkeypatch, app_module)
    candidates = pd.DataFrame(
        [{"stock_id": "2330", "close_price": 700.0, "ai_score": 0.91, "rsi": 55.0, "volume": 1000, "news_boost_reason": ""}]
    )
    monkeypatch.setattr(
        app_module,
        "_load_strategy_candidates",
        lambda **kwargs: (candidates.copy(), {"recommendation_date": "2026-09-17", "has_persisted_snapshot": True}, True),
    )
    monkeypatch.setattr(app_module, "_get_stock_mentions_map", lambda ids: {})
    monkeypatch.setattr(
        app_module,
        "_resolve_signal_news_info",
        lambda row, date, mapping: {"raw": "", "items": [], "title": "", "is_bearish": False},
    )

    response = flask_app.test_client().get(f"/api/daily-signals?strategy={STRATEGY_ID}")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["strategy_key"] == STRATEGY_ID
    assert payload["count"] == 1
    assert payload["signals"][0]["stock_id"] == "2330"
