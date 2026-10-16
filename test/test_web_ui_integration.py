from __future__ import annotations

import re
import json
import subprocess
from html.parser import HTMLParser

from flask import render_template


def _login(client):
    with client.session_transaction() as session:
        session['_user_id'] = 'admin'
        session['_fresh'] = True


def _catalog():
    return [
        {
            'strategy_id': 'hybrid_trend_rank', 'name': 'hybrid_trend_rank',
            'display_name': 'Hybrid Trend Rank', 'display_name_en': 'Hybrid Trend Rank',
            'display_name_zh': '', 'type': 'technical', 'risk_level': '', 'description': '',
            'aliases': ['V31'], 'category_key': 'trend', 'category': 'Technical',
            'lifecycle': 'PRODUCTION', 'eligibility': 'PRODUCTION', 'eligibility_reason': None,
            'available_for_daily': True, 'daily_reason': None,
            'available_for_backtest': True, 'backtest_reason': None, 'reason': None, 'active': True,
        },
        {
            'strategy_id': 'fundamental_g2g3_top5_reb60_score_weighted_v1',
            'name': 'fundamental_g2g3_top5_reb60_score_weighted_v1',
            'display_name': 'Fundamental G2/G3', 'display_name_en': 'Fundamental G2/G3',
            'display_name_zh': '', 'type': 'fundamental', 'risk_level': '', 'description': '',
            'aliases': [], 'category_key': 'fundamental', 'category': 'Fundamental',
            'lifecycle': 'SHADOW', 'eligibility': 'BLOCKED', 'eligibility_reason': 'PRODUCTION_DISABLED',
            'available_for_daily': False, 'daily_reason': 'legacy-only',
            'available_for_backtest': False, 'backtest_reason': 'legacy-only', 'reason': 'shadow', 'active': False,
        },
    ]


def test_authenticated_primary_routes_switch_with_exactly_one_active_navigation_item(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app

    monkeypatch.setattr(web_server, '_build_strategy_catalog', _catalog)
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda: ({'status': 'degraded', 'data': {'steps': []}}, 200))
    client = flask_app.test_client()
    _login(client)

    for path in ('/dashboard', '/stock-analysis?id=2330', '/strategies', '/backtest', '/system'):
        response = client.get(path)
        html = response.get_data(as_text=True)
        assert response.status_code == 200, path
        assert len(re.findall(r'class="nav-item active"', html)) == 1, path
        assert 'LIVE TRADING' not in html


def test_responsive_and_accessible_shell_structure_is_present(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app

    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda: ({'status': 'unknown', 'data': {'steps': []}}, 200))
    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=[], strategy_options=[], current_strategy='', current_mode='balanced',
        )

    assert 'class="desktop-sidebar' in html
    assert 'class="mobile-tabs"' in html
    assert 'safe-area-inset-bottom' in html
    assert 'sidebarOpen' not in html
    assert 'aria-label="手機主要導覽"' in html
    assert '<main ' in html
    assert 'focus-visible' in html
    assert '券商下單' in html and '已停用' in html


def test_dashboard_search_and_recommendation_flow_targets_stock_analysis():
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        dashboard_html = render_template(
            'dashboard.html',
            active_strategies=[], strategy_options=[], current_strategy='', current_mode='balanced',
        )
    with flask_app.test_request_context('/stock-analysis?id=2330'):
        stock_html = render_template('stock_analysis.html', stock_id='2330')

    assert "stockUrl(id)" in dashboard_html
    assert "'/stock-analysis?id='" in dashboard_html
    assert '/api/market/recommendations' in dashboard_html
    assert '/api/stock-analysis?id=' in stock_html
    assert 'LightweightCharts.createChart' in stock_html


def test_strategy_center_allows_mixed_transient_selection_but_gates_actions():
    from app import app as flask_app

    with flask_app.test_request_context('/strategies'):
        html = render_template('strategy_center.html', strategy_catalog=_catalog())

    assert 'x-model="selected"' in html
    assert 'this.selectedItems.every(item => item.available_for_daily)' in html
    assert 'this.selectedItems.every(item => item.available_for_backtest)' in html
    assert "url_for('update_strategy')" not in html
    assert 'Daily setter accepts legacy StrategyManager identities only' not in html
    assert 'window.location.href' in html
    assert 'encodeURIComponent(this.selected.join' in html


def test_json_alpine_attributes_survive_html_parsing():
    from app import app as flask_app

    class DataAttributes(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = []

        def handle_starttag(self, tag, attrs):
            self.values.extend(value for key, value in attrs if key == 'x-data')

    with flask_app.test_request_context('/strategies'):
        strategy_html = render_template('strategy_center.html', strategy_catalog=_catalog())
    with flask_app.test_request_context('/stock-analysis?id=2330'):
        stock_html = render_template('stock_analysis.html', stock_id='2330')

    parser = DataAttributes()
    parser.feed(strategy_html)
    strategy_data = next(value for value in parser.values if value.startswith('strategyCenter('))
    assert len(json.loads(strategy_data[len('strategyCenter('):-1])) == 2

    parser = DataAttributes()
    parser.feed(stock_html)
    assert 'stockAnalysisPage({ initialStockId: "2330" })' in parser.values


def test_strategy_center_javascript_gates_mixed_selection_before_actions():
    from app import app as flask_app

    with flask_app.test_request_context('/strategies'):
        html = render_template('strategy_center.html', strategy_catalog=_catalog())
    match = re.search(r'<script>\s*(function strategyCenter\(catalog\) \{.*?</script>)', html, re.S)
    assert match is not None
    script = match.group(1).replace('</script>', '')
    harness = f"""
const assert = require('node:assert/strict');
{script}
const page = strategyCenter({json.dumps(_catalog())});
page.selected = ['hybrid_trend_rank', 'fundamental_g2g3_top5_reb60_score_weighted_v1'];
assert.equal(page.dailyAllowed, false);
assert.equal(page.backtestAllowed, false);
page.selected = ['hybrid_trend_rank'];
assert.equal(page.dailyAllowed, true);
assert.equal(page.backtestAllowed, true);
console.log('strategy-gates-ok');
"""
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'strategy-gates-ok' in result.stdout


def test_strategy_center_preview_keeps_strategy_results_independent():
    from app import app as flask_app

    catalog = _catalog()
    catalog.append({**catalog[0], 'strategy_id': 'defensive_low_volatility', 'name': 'defensive_low_volatility', 'active': False})
    catalog.append({**catalog[0], 'strategy_id': 'heartbeat_strategy', 'name': 'heartbeat_strategy', 'active': False})
    catalog.append({**catalog[0], 'strategy_id': 'failed_strategy', 'name': 'failed_strategy', 'active': False})
    with flask_app.test_request_context('/strategies'):
        html = render_template('strategy_center.html', strategy_catalog=catalog)
    match = re.search(r'<script>\s*(function strategyCenter\(catalog\) \{.*?</script>)', html, re.S)
    assert match is not None
    script = match.group(1).replace('</script>', '')
    ids = [item['strategy_id'] for item in catalog]
    harness = f"""
const assert = require('node:assert/strict');
{script}
const ids = {json.dumps(ids)};
const page = strategyCenter({json.dumps(catalog)});
const originalFetch = global.fetch;
const responses = {{
  [ids[0]]: {{ freshness_status: 'stale', data: {{ recommendations: [{{stock_id: '2330'}}] }} }},
  [ids[1]]: {{ freshness_status: 'blocked', reason: 'official runtime unavailable', data: {{ recommendations: [] }} }},
  [ids[2]]: {{ freshness_status: 'current', data: {{ recommendations: [{{stock_id: '2330'}}] }} }},
  [ids[3]]: {{ freshness_status: 'heartbeat', resolution_source: 'heartbeat', recommendation_date: '2026-10-08', data: {{ recommendations: [] }} }}
}};
global.fetch = async url => {{
  const id = new URL(url, 'http://localhost').searchParams.get('strategy');
  if (id === 'failed_strategy') throw new Error('temporary failure');
  const data = responses[id];
  if (!data) throw new Error('unexpected strategy ' + id);
  return {{ok: true, json: async () => data}};
}};
(async () => {{
  page.selected = ids;
  await Promise.all(ids.map(id => page.loadStrategy(id)));
  assert.equal(page.results[ids[0]].data.freshness_status, 'stale');
  assert.match(page.resultText(ids[1]), /暫不可用/);
  assert.match(page.resultText(ids[3]), /已完成篩選/);
  assert.match(page.resultText(ids[4]), /temporary failure/);
  const sameStockStrategies = ids.filter(id => page.results[id]?.data?.data?.recommendations?.some(row => row.stock_id === '2330'));
  assert.deepEqual(sameStockStrategies, [ids[0], ids[2]]);

  let finishRace;
  global.fetch = url => new Promise(resolve => {{ finishRace = resolve; }});
  const raceId = 'removed_while_loading';
  page.selected.push(raceId);
  const pending = page.loadStrategy(raceId);
  await Promise.resolve();
  page.selected = page.selected.filter(id => id !== raceId);
  page.loadSelections(page.selected);
  finishRace({{ok: true, json: async () => ({{freshness_status: 'current', data: {{recommendations: [{{stock_id: '9999'}}]}}}})}});
  await pending;
  assert.equal(page.results[raceId], undefined);
  global.fetch = originalFetch;
  console.log('strategy-previews-isolated-ok');
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'strategy-previews-isolated-ok' in result.stdout


def test_strategy_center_renders_api_stock_rows_and_surfaces_application_errors():
    from app import app as flask_app

    with flask_app.test_request_context('/strategies'):
        html = render_template('strategy_center.html', strategy_catalog=_catalog())
    script = re.search(r'<script>\s*(function strategyCenter\(catalog\) \{.*?)</script>', html, re.S).group(1)
    rows_expression = re.search(r'<template x-for="row in \((.*?)\)" :key="row.stock_id">', html).group(1)
    harness = f"""
const assert = require('node:assert/strict');
{script}
const page = strategyCenter({json.dumps(_catalog())});
const item = {{strategy_id: 'hybrid_trend_rank'}};
page.selected = [item.strategy_id, 'fundamental_g2g3_top5_reb60_score_weighted_v1'];
global.fetch = async () => ({{ok: true, json: async () => ({{
  status: 'ok', freshness_status: 'current', recommendation_date: '2026-10-08',
  data: {{recommendations: [{{stock_id: '2330', stock_name: '台積電'}}]}}
}})}});
(async () => {{
  await page.loadStrategy(item.strategy_id);
  const results = page.results;
  const rows = {rows_expression};
  assert.deepEqual(rows.map(row => row.stock_id), ['2330']);
  assert.match(page.resultText(item.strategy_id), /1 檔/);
  global.fetch = async () => ({{ok: true, json: async () => ({{
    status: 'error', warnings: ['database temporarily unavailable'], data: {{recommendations: []}}
  }})}});
  await page.loadStrategy('fundamental_g2g3_top5_reb60_score_weighted_v1');
  assert.match(page.resultText('fundamental_g2g3_top5_reb60_score_weighted_v1'), /database temporarily unavailable/);
  assert.deepEqual(page.results[item.strategy_id].data.data.recommendations.map(row => row.stock_id), ['2330']);
  global.fetch = async () => ({{ok: true, statusText: 'OK', json: async () => ({{status: 'error'}})}});
  await page.loadStrategy('fundamental_g2g3_top5_reb60_score_weighted_v1');
  assert.match(page.resultText('fundamental_g2g3_top5_reb60_score_weighted_v1'), /推薦資料查詢失敗/);
  console.log('strategy-render-and-errors-ok');
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert 'strategy-render-and-errors-ok' in result.stdout
    for layout in ('desktop', 'mobile'):
        assert f':id="\'strategy-{layout}-\' + item.strategy_id"' in html
        assert f':for="\'strategy-{layout}-\' + item.strategy_id"' in html
    assert html.index('data-testid="strategy-results"') < html.index('data-testid="strategy-catalog-table"')
