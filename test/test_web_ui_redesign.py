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
    monkeypatch.setattr(web, '_latest_ui_market_date', lambda: '2026-10-16')
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


def test_shared_status_mapping_and_page_dependencies(client):
    import json
    import re
    from app import app
    html = client.get('/dashboard').get_data(as_text=True)
    mapping = json.loads(re.search(r'window.statusMap = (.*);', html)[1])
    macro = app.jinja_env.get_template('_ui.html').module.status_badge
    for code, (symbol, label, tone) in mapping.items():
        for variant in (code, code.upper()):
            badge = macro(variant)
            assert f'{symbol} {label}' in badge and f'status-{tone}' in badge
    assert mapping['ok'][2] == 'ok' and mapping['disabled'][2] != 'error'
    assert 'plotly-' not in html and 'lightweight-charts@' not in html
    assert 'alpinejs@3.14.9' in html
    assert 'lightweight-charts@' in client.get('/stock-analysis').get_data(as_text=True)


def test_stock_names_are_read_only_and_date_scoped(monkeypatch):
    import pandas as pd
    from sqlalchemy import create_engine
    from core import db_helper
    engine = create_engine('sqlite://')
    pd.DataFrame([
        {'trade_date': '2026-10-16', 'stock_id': '2330', 'stock_name': '台積電'},
        {'trade_date': '2026-10-15', 'stock_id': '2317', 'stock_name': '鴻海'},
    ]).to_sql('daily_market_data', engine, index=False)
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    assert db_helper.get_stock_names('2026-10-16', ['2330', '2317']) == {'2330': '台積電'}
    assert db_helper.get_stock_names('2026-10-16', []) == {}
    assert db_helper.get_stock_names(None) == {}


def test_recommendation_fills_only_missing_names(client, monkeypatch):
    import pandas as pd
    import app.web_server as web
    rows = pd.DataFrame([
        {'stock_id': '2330', 'stock_name': '', 'ai_score': 90, 'close_price': 1050},
        {'stock_id': '2317', 'stock_name': None, 'ai_score': 88, 'close_price': 210},
        {'stock_id': '2454', 'stock_name': '既有名稱', 'ai_score': 85, 'close_price': 1000},
    ])
    monkeypatch.setattr(web.services, 'get_recommendations_with_market_fallback', lambda **kw: (rows, {'recommendation_date': '2026-10-15', 'resolution_source': 'persisted'}))
    names = Mock(return_value={'2330': '台積電', '2454': '不應覆寫'})
    monkeypatch.setattr(web.db_helper, 'get_stock_names', names)
    payload = client.get('/api/market/recommendations').get_json()
    names.assert_called_once_with('2026-10-16', ['2330', '2317'])
    result = payload['data']['recommendations']
    assert [r['stock_name'] for r in result] == ['台積電', None, '既有名稱']
    assert [r['stock_id'] for r in result] == ['2330', '2317', '2454']
    assert [r['ai_score'] for r in result] == [90, 88, 85]
    assert all(r['recommendation_date'] == '2026-10-15' for r in result)


@pytest.mark.parametrize('query,expected', [('台積電', '2330'), ('台積', '2330'), ('2330', '2330'), ('台', None), ('不存在', None)])
def test_stock_name_resolution_preserves_id_path(client, monkeypatch, query, expected):
    import app.web_server as web
    names = {'2330': '台積電', '1101': '台泥'}
    monkeypatch.setattr(web.db_helper, 'get_stock_names', lambda date, ids=None: names)
    adapter = Mock(return_value={'status': 'ok', 'stock_id': '2330', 'quote': {'close_price': 1050}, 'series': {'candles': [{'time': '2026-10-16'}]}})
    monkeypatch.setattr(web.services, '_build_dashboard_health_check_payload_local', adapter)
    response = client.get('/api/stock-analysis', query_string={'id': query})
    if expected:
        assert response.status_code == 200
        assert response.get_json()['stock_id'] == expected
        assert response.get_json()['stock_name'] == '台積電'
        assert adapter.call_args.kwargs['stock_id'] == expected
    else:
        assert response.status_code == 400
        assert len(response.get_json()['candidates']) == (2 if query == '台' else 0)
        adapter.assert_not_called()


def test_preserved_backtest_alias_and_empty_selection(client):
    html = client.get('/backtest?preserved=1&strategies=v31&weights=1&persist=1').get_data(as_text=True)
    import re
    strategy = re.search(r'<input[^>]*value="hybrid_trend_rank"[^>]*>', html)[0]
    assert 'checked' in strategy
    persist = re.search(r'<input[^>]*name="persist"[^>]*>', html)[0]
    assert 'checked' in persist
    html = client.get('/backtest?preserved=1&strategies=').get_data(as_text=True)
    assert 'checked' not in re.search(r'<input[^>]*value="hybrid_trend_rank"[^>]*>', html)[0]


def test_shell_uses_freshness_evidence(client, monkeypatch):
    import app.web_server as web
    status = Mock(return_value=({'status': 'degraded'}, 200))
    monkeypatch.setattr(web, '_build_system_status_payload', status)
    assert '▲ 部分降級' in client.get('/dashboard').get_data(as_text=True)
    status.assert_called_once_with()
