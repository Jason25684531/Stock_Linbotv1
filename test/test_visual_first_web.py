"""Visual-first web (change 2026-11-16-visual-first-web-consolidation): five primary pages, cards and charts read stored values only."""
import re

import pytest

from services import smart_selection, web_dashboard


def test_a_missing_value_breaks_the_line_instead_of_drawing_zero():
    segs = web_dashboard.segments([1.0, 2.0, None, 3.0, 4.0], 400, 40)
    assert len(segs) == 2 and all(len(s.split()) == 2 for s in segs)
    assert web_dashboard.segments([None, 1.0, None], 400, 40) == []


def test_health_question_takes_the_most_severe_saved_status_and_never_upgrades_missing_data():
    item = lambda status: {'name': 'x', 'value': 0.1, 'status': status}
    report = {'health': {'earn': [item('GOOD'), item('RISK')], 'drawdown': [item('NOT_APPLICABLE'), item('INSUFFICIENT_DATA')],
                         'efficiency': [item('NOT_APPLICABLE')], 'consistency': [item('GOOD'), item('CAUTION')]}}
    strip = {h['key']: h['status'] for h in web_dashboard.health_strip(report)}
    assert strip == {'earn': 'RISK', 'drawdown': 'INSUFFICIENT_DATA', 'efficiency': 'NOT_APPLICABLE', 'consistency': 'CAUTION', 'tradability': None}


def _row(sid, cagr, trust='VERIFIED', kind='trend'):
    report = None if cagr is None else {'performance': {'verified': {'cagr': {'value': cagr}}, 'series': {'nav_base': [1.0, 1.1]}}, 'health': {}}
    return {'strategy_id': sid, 'name': sid, 'type': kind, 'type_label': kind, 'trust': trust, 'reason': None if report else 'DATA_BLOCKED',
            'report': report, 'track': {'cagr': {'value': cagr}, 'max_drawdown': {'value': -0.1}} if report else {}}


def test_wall_keeps_catalog_order_and_a_sort_puts_strategies_without_values_last():
    rows = [_row('a', 0.01), _row('b', None, 'DATA_BLOCKED'), _row('c', 0.05), _row('d', -0.02, kind='fund')]
    assert [c['strategy_id'] for c in web_dashboard.wall(rows, {})['cards']] == ['a', 'b', 'c', 'd']
    assert [c['strategy_id'] for c in web_dashboard.wall(rows, {'sort': 'cagr'})['cards']] == ['c', 'a', 'd', 'b']
    assert [c['strategy_id'] for c in web_dashboard.wall(rows, {'type': 'fund'})['cards']] == ['d']
    assert web_dashboard.wall(rows, {'sort': 'evil'})['sort'] == ''
    blocked = web_dashboard.wall(rows, {})['cards'][1]
    assert blocked['cagr'] is None and blocked['spark'] == []


def test_stock_cards_follow_consensus_and_leave_prices_empty_when_history_fails():
    view = {'status': 'OK', 'signal_date': '2026-11-13',
            'consensus': [{'stock_id': '1101', 'stock_name': 'x', 'trusted_data_signals': 2, 'unvalidated_signals': 1, 'strategies': []},
                          {'stock_id': '2330', 'stock_name': 'y', 'trusted_data_signals': 1, 'unvalidated_signals': 0, 'strategies': []}],
            'rows': [{'stock_id': '1101', 'feasibility': {'status': 'LIMITED'}}, {'stock_id': '2330', 'feasibility': {'status': 'FEASIBLE'}}]}
    def history(stock_id, day):
        if stock_id == '2330':
            raise ConnectionError('db down')
        return [10.0, 11.0]
    first, second = smart_selection.stock_cards(view, history)
    assert (first['count'], first['close'], first['feasibility']) == (3, 11.0, 'LIMITED') and first['change'] == pytest.approx(0.1)
    assert second['close'] is None and second['change'] is None and second['spark'] == []
    assert smart_selection.stock_cards({'status': 'DATA_MISSING'}, history) == []


@pytest.fixture
def client(monkeypatch):
    from app import app, web_server
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda *a, **k: ({'status': 'ok'}, 200))
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda *a, **k: None)
    return app.test_client()


@pytest.mark.parametrize('old,new', [('/reference', '/strategies'), ('/reference?history=kd_oversold', '/platform/strategies/kd_oversold#cross-regime'),
                                     ('/intelligence', '/strategies'), ('/intelligence/kd_oversold', '/platform/strategies/kd_oversold'),
                                     ('/platform', '/dashboard'), ('/platform/selection', '/dashboard'), ('/platform/strategies', '/strategies')])
def test_retired_addresses_redirect(client, old, new):
    response = client.get(old)
    assert response.status_code == 302 and response.headers['Location'].endswith(new)


def test_admin_data_is_the_system_data_tab_with_five_primary_links(client):
    html = client.get('/admin/data').get_data(as_text=True)
    sidebar = html[html.index('id="main-sidebar"'):html.index('</aside>')]
    assert len(re.findall(r'class="nav-item', sidebar)) == 5 and 'web-v3-nav' not in html
    assert re.search(r'class="nav-item active"[^>]*title="系統狀態"|title="系統狀態"[^>]*class="nav-item active"', sidebar)
    tabs = re.search(r'<nav class="vf-tabs".*?</nav>', html, re.S)[0]
    assert re.search(r'href="/admin/data" aria-current="page"', tabs) and 'href="/platform/system"' in tabs


def test_an_undefined_metric_renders_its_stored_reason_never_zero():
    from app import app
    viz = app.jinja_env.get_template('_viz.html').module
    html = str(viz.pct({'value': None, 'reason': 'drawdown has not recovered'}))
    assert 'drawdown has not recovered' in html and '%' not in html
    assert '+12.3%' in str(viz.pct({'value': 0.123, 'reason': None})) and 'vf-up' in str(viz.pct(0.123))


def test_strategy_rows_are_reused_until_a_source_folder_changes_and_refresh_after_expiry_in_background(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    out = tmp_path / 'platform_v2'
    for folder in ('runs', 'analysis', 'acceptance/publication/backtest'):
        (out / folder).mkdir(parents=True)
    routes = SimpleNamespace(OUT=out, RUNS=out / 'runs', ANALYSIS=out / 'analysis')
    intelligence = SimpleNamespace(OUT=tmp_path / 'si', _catalog=lambda: ['a'])
    calls = []
    monkeypatch.setattr(web_dashboard, '_strategies', lambda r, i: calls.append(1) or [{'n': len(calls)}])
    monkeypatch.setattr(web_dashboard, '_rows_cache', {})
    assert web_dashboard.strategies(routes, intelligence) == web_dashboard.strategies(routes, intelligence) == [{'n': 1}]
    import os
    stat = os.stat(routes.RUNS)
    (routes.RUNS / '20260101T000000Z-aaaaaaaa').mkdir()
    # a new run changes the folder's mtime; set it explicitly because Windows can keep the same tick for two quick changes
    os.utime(routes.RUNS, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert web_dashboard.strategies(routes, intelligence) == [{'n': 2}]
    key, (stamp, rows) = next(iter(web_dashboard._rows_cache.items()))
    web_dashboard._rows_cache[key] = (stamp - web_dashboard.MAX_AGE_SECONDS - 1, rows)
    assert web_dashboard.strategies(routes, intelligence) == [{'n': 2}]  # the page gets the held rows at once
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert web_dashboard.strategies(routes, intelligence) == [{'n': 3}]
