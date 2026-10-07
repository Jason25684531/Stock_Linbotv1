from __future__ import annotations

import re
import json
import subprocess

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
    assert 'md:translate-x-0' in html
    assert 'md:hidden' in html
    assert 'x-data="{ sidebarOpen:false }"' in html
    assert 'aria-controls="main-sidebar"' in html
    assert '<main ' in html
    assert 'focus-visible' in html
    assert 'BROKER' in html and 'DISABLED' in html


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
