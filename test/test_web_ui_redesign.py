"""Trading desk boundaries: reject bad input before invoking the engine."""
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from flask import flash, render_template


@pytest.fixture
def client(monkeypatch):
    import app.web_server as web
    from app import app
    from test_web_ui_integration import _catalog
    monkeypatch.setattr(web, '_build_system_status_payload', lambda **kw: ({'status': 'unknown'}, 200))
    monkeypatch.setattr(web, '_build_strategy_catalog', _catalog)
    return app.test_client()


def test_error_flash_and_strategy_redirect(client, monkeypatch):
    import app.web_server as web
    from app import app
    with app.test_request_context('/dashboard'):
        flash('測試失敗通知', 'error')
        html = render_template('dashboard.html')
    assert '測試失敗通知' in html and 'role="alert"' in html
    for success in (True, False):
        monkeypatch.setattr(web.services.strategy_manager, 'set_active_strategies', lambda ids: success)
        response = client.post('/update_strategy', data={'strategies': ['hybrid_trend_rank', 'mean_reversion']})
        assert response.location.endswith('/strategies')
        assert ('已啟用' if success else '策略切換失敗') in client.get(response.location).get_data(as_text=True)
    response = client.post('/update_strategy', data={})
    assert response.location.endswith('/strategies')
    assert '請至少選擇' in client.get(response.location).get_data(as_text=True)


@pytest.mark.parametrize('override', [
    {'end_date': '2025-01-01'}, {'start_date': ''}, {'start_date': 'invalid'},
    {'weights': 'a'}, {'weights': '-1'}, {'weights': 'nan'}, {'weights': 'inf'},
    {'weights': '1,'}, {'weights': '0.5,0.5'}, {'strategies': []},
])
def test_invalid_backtest_preserves_input_without_engine(client, monkeypatch, override):
    import app.web_server as web
    engine = Mock(side_effect=AssertionError('engine must not be called'))
    monkeypatch.setattr(web.services, '_run_portfolio_backtest', engine)
    data = {'strategies': ['hybrid_trend_rank'], 'start_date': '2026-01-01', 'end_date': '2026-10-16', 'weights': '1', 'persist': '1', **override}
    response = client.post('/backtest', data=data)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.location).query, keep_blank_values=True)
    for key in ('start_date', 'end_date', 'weights', 'persist'):
        assert query[key] == [data[key]]
    html = client.get(response.location).get_data(as_text=True)
    assert 'role="alert"' in html
    assert f'name="weights" value="{data["weights"]}"' in html
    engine.assert_not_called()


def test_engine_failure_preserves_input(client, monkeypatch):
    import app.web_server as web
    engine = Mock(side_effect=RuntimeError('測試引擎失敗'))
    monkeypatch.setattr(web.services, '_run_portfolio_backtest', engine)
    response = client.post('/backtest', data={'strategies': ['hybrid_trend_rank'], 'start_date': '2026-01-01', 'end_date': '2026-10-16', 'weights': '1'})
    assert response.status_code == 302
    engine.assert_called_once_with(['hybrid_trend_rank'], start_date='2026-01-01', end_date='2026-10-16', weights=[1.0], persist_to_db=False)
    assert '測試引擎失敗' in client.get(response.location).get_data(as_text=True)
