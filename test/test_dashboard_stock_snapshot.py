import re
import subprocess

import pandas as pd
import pytest
from flask import render_template
from sqlalchemy import create_engine

from core import db_helper


def _market_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-05-21',
                'open_price': 990,
                'close_price': 1000,
                'volume': 2000,
                'rsi': 72,
                'ma20': 950,
                'ma60': 900,
                'foreign_buy': 1200,
                'trust_buy': 300,
                'dealer_buy': 50,
                'margin_balance': 1000,
                'short_balance': 250,
            },
            {
                'stock_id': '2317',
                'trade_date': '2026-05-21',
                'open_price': 190,
                'close_price': 195,
                'volume': 1500,
                'rsi': 28,
                'ma20': 185,
                'ma60': 175,
                'foreign_buy': -800,
                'trust_buy': -200,
                'dealer_buy': -50,
                'margin_balance': 800,
                'short_balance': 40,
            },
            {
                'stock_id': '2454',
                'trade_date': '2026-05-14',
                'open_price': 800,
                'close_price': 760,
                'volume': 900,
                'rsi': 45,
                'ma20': 780,
                'ma60': 740,
                'foreign_buy': 0,
                'trust_buy': 0,
                'dealer_buy': 0,
                'margin_balance': 650,
                'short_balance': 20,
            },
            {
                'stock_id': '2330',
                'trade_date': '2026-05-14',
                'open_price': 950,
                'close_price': 940,
                'volume': 1000,
                'rsi': 60,
                'ma20': 930,
                'ma60': 890,
                'foreign_buy': 300,
                'trust_buy': 100,
                'dealer_buy': 20,
                'margin_balance': 900,
                'short_balance': 100,
            },
            {
                'stock_id': '2317',
                'trade_date': '2026-05-14',
                'open_price': 200,
                'close_price': 205,
                'volume': 1200,
                'rsi': 42,
                'ma20': 190,
                'ma60': 170,
                'foreign_buy': 100,
                'trust_buy': 50,
                'dealer_buy': 10,
                'margin_balance': 850,
                'short_balance': 60,
            },
        ]
    )


@pytest.mark.xfail(reason='pre-change dashboard tab layout retained as characterization evidence', strict=False)
def test_dashboard_template_uses_lazy_stock_snapshot_tabs():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=['v31_hybrid'],
            strategy_options=['v31_hybrid'],
            current_strategy='v31_hybrid',
            current_mode='balanced',
        )

    assert 'stockSnapshotDashboard()' in html
    assert 'activeTab' in html
    assert 'x-show="activeTab === ' in html
    assert '/api/market/summary' in html
    assert '/api/market/system-status' in html
    assert 'equityChart' not in html
    assert 'equityCurveChart' not in html
    assert '/api/performance' not in html
    assert 'loadPKBattle' not in html
    assert '/api/pk/battle' not in html


@pytest.mark.xfail(reason='pre-change dashboard detail tabs retained as characterization evidence', strict=False)
def test_dashboard_template_restores_strategy_and_stock_health_tabs():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=['v31_hybrid'],
            strategy_options=['v31_hybrid'],
            current_strategy='v31_hybrid',
            current_mode='balanced',
        )

    assert "activeTab: 'strategy'" in html
    assert "{ key: 'strategy', label: '策略選股'" in html
    assert "{ key: 'stockHealth', label: '個股健檢'" in html
    assert "x-show=\"tab.key === 'strategy'\"" in html
    assert "x-show=\"tab.key === 'stockHealth'\"" in html
    assert "stockAnalysisQuery" in html
    assert "analyzeStock()" in html
    assert "/api/stock-analysis?id=" in html


@pytest.mark.xfail(reason='pre-change dashboard tab script retained as characterization evidence', strict=False)
def test_dashboard_inline_tab_script_compiles_and_binds_direct_tab_switch():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=['v31_hybrid'],
            strategy_options=['v31_hybrid'],
            current_strategy='v31_hybrid',
            current_mode='balanced',
        )

    assert '@click="activeTab = tab.key' in html

    match = re.search(r'<script>\s*(document\.addEventListener\(\'alpine:init\', .*?)</script>', html, re.S)
    assert match is not None
    script = match.group(1)
    result = subprocess.run(
        ['node', '-e', "let s=''; process.stdin.setEncoding('utf8'); process.stdin.on('data', d => s += d); process.stdin.on('end', () => { new Function(s); console.log('ok'); });"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'ok' in result.stdout


def test_dashboard_template_is_summary_only_after_restructure():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=['v31_hybrid'],
            strategy_options=['v31_hybrid'],
            current_strategy='v31_hybrid',
            current_mode='balanced',
        )

    assert 'dashboardSummary()' in html
    assert 'global-stock-search' in html
    assert '今日推薦' in html
    assert '啟用策略' in html
    assert '/stock-analysis?id=' in html
    assert 'LightweightCharts.createChart' not in html
    assert '<pre' not in html
    assert 'stockSnapshotDashboard' not in html


def test_dashboard_inline_summary_script_contains_browser_search_and_no_chart():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=[],
            strategy_options=[],
            current_strategy='',
            current_mode='balanced',
        )

    assert 'Alpine.data' in html
    assert 'localStorage' in html
    assert 'method="get" action="/stock-analysis"' in html
    assert '/api/market/recommendations' in html
    assert '/api/market/institutional' not in html
    assert '/api/market/margin' in html
    assert '>技術面<' in html
    assert '>融資融券<' in html
    match = re.search(r'<script>\s*(document\.addEventListener\(\'alpine:init\', .*?)</script>', html, re.S)
    assert match is not None
    result = subprocess.run(
        ['node', '-e', "let s=''; process.stdin.setEncoding('utf8'); process.stdin.on('data', d => s += d); process.stdin.on('end', () => { new Function(s); console.log('ok'); });"],
        input=match.group(1), text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_dashboard_recommendation_metadata_handles_normal_and_degraded_sources():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template('dashboard.html')
    match = re.search(r'<script>\s*(document\.addEventListener\(\'alpine:init\', .*?)</script>', html, re.S)
    assert match is not None
    harness = r"""
const assert = require('node:assert/strict');
const factories = {};
global.document = {addEventListener(name, callback) { if (name === 'alpine:init') callback(); }};
global.Alpine = {data(name, factory) { factories[name] = factory; }};
__SCRIPT__
const page = factories.dashboardSummary();
page.recommendationLoading = false;
page.recommendationMeta = {status: 'ok', source: ['daily_recommendations'], recommendation_date: '2026-10-08', as_of_date: '2026-10-08'};
page.recommendations = [{stock_id: '2330', fallback_used: false}];
assert.equal(page.recommendationStatus(), 'NORMAL');
assert.equal(page.recommendationSource(), 'daily_recommendations');
assert.equal(page.recommendationDate(), '2026-10-08');
page.recommendationMeta = {status: 'degraded', source: ['local_cache'], as_of_date: '2026-10-07'};
page.recommendations = [{stock_id: '2330', fallback_used: true}];
assert.equal(page.recommendationStatus(), 'DEGRADED');
assert.equal(page.recommendationSource(), '本機備援');
page.recommendationMeta = {status: 'degraded', freshness_status: 'stale', last_available_recommendation_date: '2026-06-26'};
page.recommendations = [];
assert.equal(page.recommendationStatus(), 'STALE');
assert.equal(page.recommendationCount(), '資料已過期');
assert.equal(page.recommendationDate(), '2026-06-26');
assert.match(page.recommendationMessage(), /不視為今日訊號/);
page.recommendationMeta = {status: 'empty', as_of_date: '2026-10-08'};
page.recommendations = [];
assert.equal(page.recommendationCount(), '無可用推薦');
assert.equal(page.recommendationDate(), '尚無推薦紀錄');
assert.match(page.recommendationMessage(), /2026-10-08/);
page.recommendationMeta = {status: 'error'};
assert.equal(page.recommendationCount(), '資料暫不可用');
assert.equal(page.recommendationDate(), '資料暫不可用');
console.log('recommendation-meta-ok');
""".replace('__SCRIPT__', match.group(1))
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'recommendation-meta-ok' in result.stdout


def test_dashboard_clears_previous_recommendations_after_refresh_failure():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template('dashboard.html')
    match = re.search(r'<script>\s*(document\.addEventListener\(\'alpine:init\', .*?)</script>', html, re.S)
    assert match is not None
    harness = r"""
const assert = require('node:assert/strict');
const factories = {};
global.document = {addEventListener(name, callback) { if (name === 'alpine:init') callback(); }};
global.Alpine = {data(name, factory) { factories[name] = factory; }};
__SCRIPT__
const page = factories.dashboardSummary();
page.recommendationMeta = {status: 'ok', recommendation_date: '2026-10-08'};
page.recommendations = [{stock_id: '2330'}];
page.strategies = {active: ['hybrid_trend_rank']};
global.fetch = async () => { throw new Error('network down'); };
(async () => {
  await page.loadRecommendations();
  assert.deepEqual(page.recommendations, []);
  assert.deepEqual(page.recommendationMeta, {});
  assert.equal(page.recommendationError, 'network down');
  assert.equal(page.recommendationCount(), '資料暫不可用');
  assert.equal(page.recommendationDate(), '資料暫不可用');
  console.log('recommendation-refresh-error-ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace('__SCRIPT__', match.group(1))
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'recommendation-refresh-error-ok' in result.stdout


def test_dashboard_keeps_successful_strategy_when_another_fails():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template('dashboard.html')
    match = re.search(r'<script>\s*(document\.addEventListener\(\'alpine:init\', .*?)</script>', html, re.S)
    assert match is not None
    harness = r"""
const assert = require('node:assert/strict');
const factories = {};
global.document = {addEventListener(name, callback) { if (name === 'alpine:init') callback(); }};
global.Alpine = {data(name, factory) { factories[name] = factory; }};
__SCRIPT__
const page = factories.dashboardSummary();
page.strategies = {active: ['hybrid_trend_rank', 'defensive_low_volatility']};
global.fetch = async url => {
  if (url.includes('defensive_low_volatility')) throw new Error('strategy unavailable');
  return {ok: true, json: async () => ({status: 'ok', data: {recommendations: [{stock_id: '2330'}]}, recommendation_date: '2026-10-08', freshness_status: 'current'})};
};
(async () => {
  await page.loadRecommendations();
  assert.deepEqual(page.recommendations.map(row => [row.stock_id, row.strategy_id]), [['2330', 'hybrid_trend_rank']]);
  assert.equal(page.recommendationMeta.status, 'degraded');
  assert.deepEqual(page.recommendationMeta.warnings, ['defensive_low_volatility: strategy unavailable']);
  console.log('dashboard-strategy-failure-isolated-ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
""".replace('__SCRIPT__', match.group(1))
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'dashboard-strategy-failure-isolated-ok' in result.stdout


def test_market_recommendations_exposes_stale_data_without_relabeling(monkeypatch):
    import app.web_server as web_server_module
    import app as app_module
    from app import app as flask_app

    monkeypatch.setattr(web_server_module.db_helper, 'get_latest_valid_market_date', lambda **kwargs: '2026-10-08')
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (pd.DataFrame(), {
            'recommendation_date': '2026-10-08',
            'last_available_recommendation_date': '2026-06-26',
            'fallback_used': False,
            'fallback_too_old': True,
            'fallback_age_days': 104,
            'resolution_source': 'missing',
        }),
    )

    payload = flask_app.test_client().get('/api/market/recommendations').get_json()

    assert payload['status'] == 'degraded'
    assert payload['as_of_date'] == '2026-10-08'
    assert payload['freshness_status'] == 'stale'
    assert payload['last_available_recommendation_date'] == '2026-06-26'
    assert payload['data']['recommendations'] == []


def test_market_recommendations_rejects_unknown_strategy():
    from app import app as flask_app

    response = flask_app.test_client().get('/api/market/recommendations?strategy=unknown_strategy')

    assert response.status_code == 400
    assert response.get_json()['status'] == 'error'


def test_market_pages_anchor_to_latest_market_date_not_market_recommendation_intersection(monkeypatch):
    import app as app_module
    import app.web_server as web_server_module

    requested_dates = []
    market = _market_frame()
    monkeypatch.setattr(web_server_module, '_latest_ui_market_date', lambda: '2026-10-08')
    monkeypatch.setattr(app_module, '_resolve_ui_baseline_date', lambda: '2026-06-26')
    monkeypatch.setattr(app_module, 'get_stock_data', lambda date_str=None: (requested_dates.append(date_str) or market.copy(), date_str))

    with app_module.app.test_request_context('/api/market/summary'):
        frame, as_of_date, _ = web_server_module._load_market_frame()

    assert requested_dates == ['2026-10-08']
    assert as_of_date == '2026-05-21'


def test_stock_analysis_api_adapts_health_check_payload(monkeypatch):
    import app as app_module
    from app import app as flask_app

    def fake_builder(stock_id, requested_date=None, period=None, overlays=None, panes=None, basic_only=False):
        return {
            'status': 'ok',
            'symbol': stock_id,
            'as_of_date': '2026-05-21',
            'source': ['daily_market_data'],
            'warnings': [],
            'quote': {'close_price': 1000, 'trade_date': '2026-05-21'},
            'series': {'candles': [{'time': '2026-05-21', 'close': 1000}], 'ma20': [{'time': '2026-05-21', 'value': 950}]},
            'indicators': {'rsi': 72, 'ma20': 950, 'chip_score': 0.8},
            'institutional': {'foreign_buy': 1200, 'trust_buy': 300, 'dealer_buy': 50},
            'rule_report': {
                'summary': '多方續強',
                'trend': '站上均線',
                'chips': '法人偏多',
                'action_scripts': [{'label': '隔日操作劇本', 'text': '回測不破可續抱'}],
            },
            'war_room': {'chip_flow': {'source_mode': 'direct'}, 'price_map': {'support': 950}},
        }

    monkeypatch.setattr(app_module, '_build_dashboard_health_check_payload_local', fake_builder)

    response = flask_app.test_client().get('/api/stock-analysis?id=2330')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'ok'
    assert payload['stock_id'] == '2330'
    assert payload['as_of_date'] == '2026-05-21'
    assert payload['quote']['close_price'] == 1000
    assert payload['kline_ma']['candles'][0]['close'] == 1000
    assert payload['technical']['rsi'] == 72
    assert payload['chip']['institutional']['foreign_buy'] == 1200
    assert payload['action_script'][0]['label'] == '隔日操作劇本'


def test_stock_analysis_api_requires_stock_id():
    from app import app as flask_app

    response = flask_app.test_client().get('/api/stock-analysis')
    payload = response.get_json()

    assert response.status_code == 400
    assert payload['status'] == 'error'
    assert 'id' in payload['error']


def test_stock_analysis_api_marks_missing_quote_or_kline_as_degraded(monkeypatch):
    import app as app_module
    from app import app as flask_app

    def fake_builder(stock_id, requested_date=None, period=None, overlays=None, panes=None, basic_only=False):
        return {
            'status': 'ok',
            'symbol': stock_id,
            'as_of_date': '2026-05-21',
            'source': ['daily_market_data'],
            'warnings': [],
            'quote': {},
            'series': {'candles': []},
            'indicators': {},
            'institutional': {},
            'rule_report': {'action_scripts': []},
            'war_room': {},
        }

    monkeypatch.setattr(app_module, '_build_dashboard_health_check_payload_local', fake_builder)

    response = flask_app.test_client().get('/api/stock-analysis?id=2330')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'degraded'
    assert payload['stock_id'] == '2330'
    assert payload['warnings']


def test_market_snapshot_endpoints_return_normalized_envelopes(monkeypatch):
    import app as app_module
    from app import app as flask_app

    market_df = _market_frame()
    recommendations_df = pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'close_price': 1000,
                'ai_score': 0.91,
                'rsi': 72,
                'volume': 2000,
                'chip_score': 0.8,
                'foreign_buy': 1200,
                'strategy': 'v36_chip_momentum',
                'rsi_z_score': 1.2,
            }
        ]
    )

    monkeypatch.setattr(app_module, '_current_line_date', lambda: '2026-05-21')
    monkeypatch.setattr(app_module, '_resolve_ui_baseline_date', lambda: '2026-05-21')
    import app.web_server as web_server_module
    monkeypatch.setattr(web_server_module.db_helper, 'get_latest_valid_market_date', lambda **kwargs: '2026-05-21')
    monkeypatch.setattr(app_module, 'get_stock_data', lambda date_str=None, stock_id=None: (market_df.copy(), '2026-05-21'))
    monkeypatch.setattr(app_module, 'get_daily_recommendations', lambda **kwargs: recommendations_df.copy())
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (recommendations_df.copy(), {
            'recommendation_date': '2026-05-21', 'fallback_used': False,
            'resolution_source': 'persisted', 'current_day_recommendations_used': True,
        }),
    )

    client = flask_app.test_client()

    endpoints = [
        '/api/market/summary',
        '/api/market/recommendations',
        '/api/market/snapshot',
        '/api/market/institutional',
        '/api/market/technical',
        '/api/market/margin',
    ]
    for endpoint in endpoints:
        response = client.get(endpoint)
        payload = response.get_json()

        assert response.status_code == 200
        assert payload['status'] in {'ok', 'degraded', 'empty'}
        assert payload['as_of_date'] == '2026-05-21'
        assert isinstance(payload['source'], list)
        assert isinstance(payload['warnings'], list)
        assert isinstance(payload['data'], dict)

    assert client.get('/api/market/summary').get_json()['data']['market_light']['state'] == 'bullish'
    recommendation = client.get('/api/market/recommendations').get_json()['data']['recommendations'][0]
    assert recommendation['ai_score'] == 0.91
    assert recommendation['factor_fields']['rsi_z_score'] == 1.2
    assert client.get('/api/market/snapshot').get_json()['data']['breadth']['up_count'] == 2
    assert client.get('/api/market/institutional').get_json()['data']['totals']['total_net'] == 500
    assert client.get('/api/market/technical').get_json()['data']['rsi_overbought'][0]['stock_id'] == '2330'
    assert client.get('/api/market/margin').get_json()['data']['high_short_margin_risk'][0]['stock_id'] == '2330'


def test_market_system_status_maps_pipeline_runs(monkeypatch):
    import core.db_helper as db_helper_module
    from app import app as flask_app

    engine = create_engine('sqlite:///:memory:')
    db_helper.ensure_pipeline_run_state_schema(engine)
    for step_name, status in (
        ('update_database', 'success'),
        ('run_daily', 'failed'),
        ('push_to_line', 'success'),
    ):
        db_helper.record_pipeline_step_finish(
            pipeline_name='daily',
            step_name=step_name,
            run_date='2026-05-21',
            status=status,
            trade_date='2026-05-21',
            rows_inserted=10,
            engine=engine,
            error_summary='boom' if status == 'failed' else None,
        )

    monkeypatch.setattr(db_helper_module, 'get_db_engine', lambda: engine)

    response = flask_app.test_client().get('/api/market/system-status')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'degraded'
    assert payload['data']['steps'][0]['alias'] == '1_update'
    assert payload['data']['steps'][0]['status'] == 'Success'
    assert payload['data']['steps'][1]['alias'] == '2_run'
    assert payload['data']['steps'][1]['status'] == 'Failed'
    assert payload['data']['steps'][1]['error_summary'] == 'boom'
    assert payload['data']['steps'][2]['alias'] == '5_push'
    assert payload['data']['steps'][2]['status'] == 'Success'


def test_manual_recovery_does_not_hide_official_pipeline_failure(monkeypatch):
    from app import app as flask_app, web_server

    engine = create_engine('sqlite:///:memory:')
    db_helper.record_pipeline_step_finish(
        pipeline_name='daily', step_name='update_database', run_date='2026-10-13',
        status='failed', error_summary='official fetch failed', engine=engine,
    )
    for step_name in ('update_database', 'run_daily'):
        db_helper.record_pipeline_step_finish(
            pipeline_name='manual', step_name=step_name, run_date='2026-10-14',
            status='success', trade_date='2026-10-13', engine=engine,
        )
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda: '2026-10-13')
    monkeypatch.setattr(web_server.services, '_resolve_ui_baseline_date', lambda: '2026-10-13')

    payload = flask_app.test_client().get('/api/market/system-status').get_json()
    assert payload['status'] == 'degraded'
    assert payload['data']['heartbeat'] == {'state': 'failed', 'latest_run_date': '2026-10-13'}
    assert [step['status'] for step in payload['data']['steps']] == ['Failed', 'Unknown', 'Unknown']
    assert payload['data']['steps'][0]['error_summary'] == 'official fetch failed'
    assert payload['data']['recommendation_freshness'] == {
        'status': 'current', 'market_date': '2026-10-13', 'recommendation_date': '2026-10-13',
    }
