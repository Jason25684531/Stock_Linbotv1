from __future__ import annotations

from flask import render_template


def test_shared_shell_contains_primary_navigation_and_truthful_status(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app

    monkeypatch.setattr(
        web_server,
        "_build_system_status_payload",
        lambda: ({"status": "degraded", "data": {"heartbeat": {}}}, 200),
    )
    with flask_app.test_request_context("/dashboard"):
        html = render_template(
            "dashboard.html",
            active_strategies=[],
            strategy_options=[],
            current_strategy="",
            current_mode="balanced",
        )

    assert 'id="main-sidebar"' in html
    assert 'href="/dashboard"' in html
    assert 'href="/stock-analysis"' in html
    assert 'href="/strategies"' in html
    assert 'href="/backtest"' in html
    assert 'href="/system"' in html
    assert "DEGRADED" in html
    assert "BROKER" in html and "DISABLED" in html
    assert "LIVE TRADING" not in html


def test_primary_pages_open_without_authentication():
    from app import app as flask_app

    client = flask_app.test_client()
    for path in ("/dashboard", "/stock-analysis", "/strategies", "/backtest", "/system"):
        response = client.get(path)
        assert response.status_code == 200
