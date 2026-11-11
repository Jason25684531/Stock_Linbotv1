"""Web surfaces of change 2026-11-10-historical-data-foundation-v1: values equal the stored evaluation, filters, guest read-only, no ranking."""
import json

import pytest

from core.research.history import snapshot as hs
from test_history_snapshot import sources  # noqa: F401  (fixture)


@pytest.fixture
def stored(sources, tmp_path, monkeypatch):
    from scripts import historical_foundation as hf
    from scripts import historical_report
    out = tmp_path / 'hf'
    for name, value in {'OUT': out, 'EVALUATIONS': out / 'evaluations', 'REPORTS': out / 'reports', 'RUNS': out / 'runs'}.items():
        monkeypatch.setattr(hf, name, value)
    monkeypatch.setattr(historical_report, 'DOCS', tmp_path / 'docs')
    snap, _ = hs.build(sources, out / 'snapshots')
    evaluation = hf.evaluate(snap)
    hf.gaps(evaluation)
    historical_report.data_reports(evaluation)
    return evaluation


def client():
    from app import app
    return app.test_client()


def test_admin_data_shows_stored_grid_for_guest_without_actions(stored):
    html = client().get('/admin/data').get_data(as_text=True)
    assert 'data-testid="historical-dashboard"' in html and stored['snapshot_id'] in html and stored['evaluation_id'] in html
    assert 'data-testid="history-refresh"' not in html and 'data-testid="history-run"' not in html


def test_status_filter_limits_gap_list(stored):
    html = client().get('/admin/data?status=VERIFIED').get_data(as_text=True)
    gaps = html[html.index('data-testid="historical-gaps"'):html.index('data-testid="historical-sources"')]
    assert 'data-status="ACCESS_BLOCKED"' not in gaps and 'data-status="PARTIAL"' not in gaps


def test_unknown_filter_values_are_ignored(stored):
    assert client().get('/admin/data?status=<script>&category=../../x').status_code == 200


def test_strategy_center_carries_history_strip(stored):
    from app import web_server
    captured = {}
    original = web_server.render_template
    monkeypatch_render = lambda name, **kw: captured.update(kw) or original(name, **kw)
    web_server.render_template = monkeypatch_render
    try:
        html = client().get('/strategies').get_data(as_text=True)
    finally:
        web_server.render_template = original
    assert 'data-testid="history-trust"' in html
    items = {i['strategy_id']: i for i in captured['strategy_catalog']}
    assert [c['status'] for c in items['bollinger_reversion']['history']['cells']] == [c['status'] for c in stored['trust']['bollinger_reversion']]


def test_detail_page_lists_eleven_years_and_requirements(stored):
    html = client().get('/strategies/bollinger_reversion/data-trust').get_data(as_text=True)
    years = html[html.index('data-testid="trust-years"'):]
    assert all(f'>{y}<' in years for y in range(2015, 2026))
    assert 'data-testid="requirement-row"' in html and 'data-testid="data-trust"' in html


def test_governed_detail_page(stored):
    html = client().get('/strategies/H4:GROWTH-MOMENTUM-V2/data-trust').get_data(as_text=True)
    assert 'data-status="GOVERNED_ONLY"' in html


def test_unknown_strategy_is_404(stored):
    assert client().get('/strategies/not_a_strategy/data-trust').status_code == 404


def test_guest_cannot_start_history_jobs(stored, monkeypatch):
    from core.research.reference import batch_job
    started = []
    monkeypatch.setattr(batch_job, 'start', lambda **kw: started.append(kw) or {'status': 'STARTED'})
    for action in ('refresh', 'run', 'cancel'):
        assert client().post(f'/admin/history/{action}').status_code in (401, 403)
    assert started == []


def test_admin_without_csrf_cannot_start_history_jobs(stored, monkeypatch):
    from app import platform_routes
    from core.research.reference import batch_job
    started = []
    monkeypatch.setattr(batch_job, 'start', lambda **kw: started.append(kw) or {'status': 'STARTED'})
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    assert client().post('/admin/history/run', data={'csrf': 'wrong'}).status_code in (400, 403)
    assert started == []


def test_downloads_are_markdown_whitelisted(stored):
    c = client()
    ok = c.get(f"/historical/reports/{stored['evaluation_id']}/HISTORICAL_DATA_COVERAGE_REPORT.md")
    assert ok.status_code == 200 and ok.mimetype == 'text/markdown'
    assert c.get(f"/historical/reports/{stored['evaluation_id']}/HISTORICAL_DATA_COVERAGE_REPORT.json").status_code == 404
    assert c.get('/historical/reports/..%2F..%2Fx/gap_report.md').status_code == 404


def test_reference_page_without_history_report_says_so(stored):
    html = client().get('/reference').get_data(as_text=True)
    assert 'data-testid="cross-regime-empty"' in html


def test_reference_page_cross_regime_in_catalog_order_without_ranking(stored, monkeypatch, tmp_path):
    from scripts import historical_foundation as hf
    from scripts import historical_report
    import pandas as pd
    dates = pd.bdate_range('2024-01-02', periods=3).strftime('%Y-%m-%d')
    nav = pd.DataFrame({'date': dates, 'nav_base': [1e6, 1.01e6, 1.02e6], 'nav_stress': [1e6, 1.0e6, 1.01e6]})
    trades = pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct'])
    fake = lambda row: {'nav': nav, 'trades': trades, 'engine_error': None, 'accounting': hf.ACCOUNTING}
    result = hf.run(execute=fake, evaluation=stored, root=hf.RUNS)
    historical_report.regime_reports(result['rows'], stored, hf.RUNS)
    html = client().get('/reference').get_data(as_text=True)
    section = html[html.index('id="cross-regime"'):]
    assert 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES' in section and 'data-testid="cross-regime-overview"' in section
    ids = [s['strategy_id'] for s in json.loads((hf.REPORTS / json.loads((hf.REPORTS / 'LATEST.json').read_text(encoding='utf-8'))['cross_regime']).read_text(encoding='utf-8'))['strategies']]
    from core.research.reference.catalog import capabilities
    assert ids == [c.strategy_id for c in capabilities()]
    assert '<form' not in section.lower() and 'data-sort' not in section.lower()


# ---------------------------------------------------------------- engine accounting fix round (state.json, evidence.json, legacy vs new)

@pytest.fixture
def fix_dir(tmp_path, monkeypatch):
    """An empty outputs/engine_history_fix stand-in; the pages must render before any producer wrote into it."""
    from app import platform_routes
    from core.research.history import taskstate
    folder = tmp_path / 'engine_history_fix'
    folder.mkdir()
    monkeypatch.setattr(platform_routes, 'ENGINE_FIX', folder)
    monkeypatch.setattr(taskstate, 'STATE', folder / 'state.json')
    return folder


SHA = 'ab12cd34ef56' + '0' * 52
EVIDENCE = {
    'saturdays': {'20160130': {'file': 'MI_INDEX_20160130.json', 'sha256': SHA, 'bytes': 10, 'payload_date': '20160130', 'rows': 900,
                               'traded_rows': 880, 'in_snapshot': True, 'finlab': {'compared': 850, 'agreed': 850}}},
    'capital_changes_2023_2025': {'TWTAUU': {'file': 'TWTAUU_2023.csv', 'sha256': 'c' * 64, 'rows': 12, 'from': 'extracts/twtauu.csv', 'from_sha256': 'd' * 64},
                                  'unexplained_jumps_before': 7, 'unexplained_jumps_after': 1, 'explained_now': ['2330 2024-07-01']},
    'delisted_2015': {'2384': {'name': 'A', 'delisting_date': '2014-12-31', 'source': 'TWSE', 'mi_index_files_2015_containing': 0, 'finlab_last_close_date': '2014-12-30'},
                      '5280': {'name': 'B', 'delisting_date': None, 'source': 'TWSE', 'mi_index_files_2015_containing': 0, 'finlab_last_close_date': None},
                      'verdict': 'NOT_IN_LISTED_UNIVERSE_2015'},
    'benchmark_2019': {'sessions_without_index_before': 1, 'sessions_without_index_after': 0, 'fixed_date': '2019-04-29', 'taiex_price': 10983.0,
                       'taiex_total_return': 19500.5, 'method': 'MI_INDEX row'},
    'budget': {'hist_foundation_quotes': {'used': 8, 'cap': 10}, 'hist_foundation_events': {'used': 1, 'cap': 4}, 'hist_foundation_verify': {'used': 0, 'cap': 3},
               'total_cap': 17, 'http_requests_this_round': 9},
    'snapshot': {'legacy': 'HS1-legacy', 'new': 'HS1-new'}, 'engine': {'legacy': 'LEGACY_RAW_PRICE', 'new': 'POSITION_LEDGER_CA_V1'}}


def _write_round(folder):
    from core.research.history import taskstate
    (folder / 'evidence.json').write_text(json.dumps(EVIDENCE), encoding='utf-8')
    state = folder / 'state.json'
    taskstate.update('F01', 'PASS', title='legacy baseline', checkpoint='legacy_baseline.json', path=state)
    taskstate.update('F02', 'BLOCKED', title='owner grant', error_class='AUTHORIZATION', retryable=False, note='needs Owner grant', path=state)
    taskstate.update('S:bollinger_reversion', 'RUNNING', title='bollinger_reversion', path=state)
    taskstate.update('S:bollinger_reversion', 'RETRYING', error_class='CODE_DEFECT', retryable=True, note='nav mismatch', path=state)
    taskstate.update('S:bollinger_reversion', 'FAILED', error_class='CODE_DEFECT', retryable=False, path=state)


def test_admin_data_renders_fix_sections_before_the_round_wrote_anything(stored, fix_dir):
    response = client().get('/admin/data')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'data-testid="fix-evidence"' in html and 'data-testid="fix-tasks"' in html
    assert 'evidence.json' in html and 'state.json' in html  # the not-yet-available notices name the missing file
    assert 'data-testid="fix-task-row"' not in html and '/engine-fix/' not in html


def test_admin_data_renders_round_evidence_and_task_state_read_only_for_guests(stored, fix_dir):
    _write_round(fix_dir)
    html = client().get('/admin/data').get_data(as_text=True)
    evidence = html[html.index('data-testid="fix-evidence"'):html.index('data-testid="fix-tasks"')]
    assert SHA[:12] in evidence and SHA[:20] not in evidence and '>900<' in evidence and 'TWTAUU' in evidence
    assert 'NOT_IN_LISTED_UNIVERSE_2015' in evidence and '2019-04-29' in evidence and '>8 / 10<' in evidence and 'HS1-legacy' in evidence
    tasks = html[html.index('data-testid="fix-tasks"'):]
    assert tasks.count('data-testid="fix-task-row"') == 3
    assert 'data-status="BLOCKED"' in tasks and 'data-status="FAILED"' in tasks and 'needs Owner grant' in tasks and 'AUTHORIZATION' in tasks
    row = tasks[tasks.index('data-task="S:bollinger_reversion"'):]
    assert '>1<' in row  # one repair round counted before the failure
    assert 'data-testid="history-run"' not in html and 'data-testid="history-refresh"' not in html
    assert 'href="/engine-fix/evidence.json"' in html and 'href="/engine-fix/state.json"' in html


def test_admin_sees_history_buttons_next_to_task_state(stored, fix_dir, monkeypatch):
    from app import platform_routes
    _write_round(fix_dir)
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    html = client().get('/admin/data').get_data(as_text=True)
    assert 'data-testid="history-run"' in html and 'data-testid="history-refresh"' in html and 'data-testid="fix-tasks"' in html


def test_engine_fix_downloads_are_allowlisted(fix_dir):
    c = client()
    assert c.get('/engine-fix/evidence.json').status_code == 404
    assert c.get('/engine-fix/FINAL_ENGINE_AND_HISTORY_VALIDATION_REPORT.md').status_code == 404
    _write_round(fix_dir)
    (fix_dir / 'refresh_1.log').write_text('secret', encoding='utf-8')
    (fix_dir / 'FINAL_ENGINE_AND_HISTORY_VALIDATION_REPORT.md').write_text('# final', encoding='utf-8')
    ok = c.get('/engine-fix/FINAL_ENGINE_AND_HISTORY_VALIDATION_REPORT.md')
    assert ok.status_code == 200 and ok.mimetype == 'text/markdown' and ok.get_data(as_text=True) == '# final'
    assert c.get('/engine-fix/state.json').status_code == 200 and c.get('/engine-fix/evidence.json').mimetype == 'application/json'
    assert c.get('/engine-fix/refresh_1.log').status_code == 404
    assert c.get('/engine-fix/..%2Flegacy_baseline.json').status_code == 404


def _catalog_items(path):
    from app import web_server
    captured = {}
    original = web_server.render_template
    web_server.render_template = lambda name, **kw: captured.update(kw) or original(name, **kw)
    try:
        html = client().get(path).get_data(as_text=True)
    finally:
        web_server.render_template = original
    return html, {i['strategy_id']: i for i in captured['strategy_catalog']}


def test_pages_show_legacy_vs_new_comparison_only_when_the_report_carries_it(stored, fix_dir, monkeypatch):
    from app import platform_routes
    from scripts import historical_foundation as hf
    from scripts import historical_report
    import pandas as pd
    dates = pd.bdate_range('2024-01-02', periods=3).strftime('%Y-%m-%d')
    nav = pd.DataFrame({'date': dates, 'nav_base': [1e6, 1.01e6, 1.02e6], 'nav_stress': [1e6, 1.0e6, 1.01e6]})
    trades = pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct'])
    from core.research.history import regime
    from core.research.reference.catalog import capabilities
    regime.register(hf.OUT)
    sid = 'defensive_low_volatility'  # the fixture sources block every strategy, so one stored run is written through the real save path
    rows = hf.plan(stored, root=hf.RUNS)
    row = next(r for r in rows if r['strategy_id'] == sid)
    row.update(state='RUN', reason='NO_STORED_RUN', window=['2024-01-02', '2024-01-04'], run_identity='RID1:' + 'e' * 64)
    cap = next(c for c in capabilities() if c.strategy_id == sid)
    row['run_id'] = hf.save(cap, row, {'nav': nav, 'trades': trades, 'engine_error': None, 'accounting': hf.ACCOUNTING}, stored['snapshot_id'], root=hf.RUNS)
    historical_report.regime_reports(rows, stored, hf.RUNS)
    path = hf.REPORTS / json.loads((hf.REPORTS / 'LATEST.json').read_text(encoding='utf-8'))['cross_regime']
    report = json.loads(path.read_text(encoding='utf-8'))
    assert next(s for s in report['strategies'] if s['strategy_id'] == sid)['analysis']

    before = client().get(f'/reference?history={sid}').get_data(as_text=True)  # whatever the report builder emitted: renders, no crash
    assert 'data-testid="legacy-compare"' in before and 'data-testid="compare-table"' in before
    assert 'POSITION_LEDGER_CA_V1' in before  # accounting read from the stored run.json
    html, items = _catalog_items('/strategies')
    assert 'data-testid="engine-compare"' in html and {'final_state', 'accounting', 'compare', 'usable_years'} <= set(items[sid]['history'])

    sel = next(s for s in report['strategies'] if s['strategy_id'] == sid)
    years = lambda v: {'2024': v}
    sel.update(final_state='NEW_RUN_COMPLETE', accounting='POSITION_LEDGER_CA_V1', decision={**sel['decision'], 'state': 'GOVERNED_VALIDATION_REQUIRED'},
               legacy_comparison={'legacy_run_id': 'L1', 'legacy_snapshot_id': 'HS1-legacy', 'legacy_accounting': 'LEGACY_RAW_PRICE',
                                  'window_legacy': ['2024-01-02', '2024-01-04'], 'window_new': ['2024-01-02', '2024-01-04'],
                                  'common_years': ['2024'],
                                  'legacy': {'common_years_return': 0.1, 'base_total_return': 0.1, 'x2_total_return': 0.08, 'max_drawdown_base': -0.05, 'sharpe_base': 1.1, 'trades': 3, 'years': years(0.1)},
                                  'new': {'common_years_return': 0.125, 'base_total_return': 0.125, 'x2_total_return': None, 'max_drawdown_base': -0.04, 'sharpe_base': 1.3, 'trades': 3, 'years': years(0.125)},
                                  'delta': {'common_years_return': 0.025, 'base_total_return': 0.025, 'x2_total_return': None, 'max_drawdown_base': 0.01, 'sharpe_base': 0.2, 'trades': 0, 'years': years(0.025)},
                                  'attribution': {'cash_credited': 1234.5, 'events_booked': 2, 'assumed_stock_events': 0, 'unresolved_events': 0,
                                                  'saturday_sessions_added': 1, 'benchmark_sessions_labelled_added': 1, 'reasons': ['DIVIDEND_CASH_CREDITED']}},
               open_positions={'count': 1, 'value': 1000.0, 'rows': [{'stock_id': '2330', 'shares': 1000, 'entry_price': 500.0, 'last_mark_value': 1000.0, 'distributions': 12.0, 'buy_date': '2024-01-03'}]},
               corporate_action_impact={'events_booked': 2, 'cash_credited': 1234.5, 'assumed_stock_events': 0, 'unresolved_events': 0, 'duplicate_suppressed': 1, 'years_with_assumed_terms': [2024]})
    report['legacy_comparison_table'] = [{'strategy_id': s['strategy_id'], 'final_state': s.get('final_state', s['state']), 'accounting': s.get('accounting'),
                                          'legacy_base_total_return': 0.1 if s is sel else None, 'new_base_total_return': 0.125 if s is sel else None,
                                          'delta': 0.025 if s is sel else None, 'reason': s.get('reason') or ''} for s in report['strategies']]
    path.write_text(json.dumps(report), encoding='utf-8')
    platform_routes._stored_json.cache_clear()

    html = client().get(f'/reference?history={sid}').get_data(as_text=True)
    block = html[html.index('data-testid="legacy-compare"'):html.index('data-testid="history-nav-chart"')]
    assert 'NEW_RUN_COMPLETE' in block and '10.00%' in block and '12.50%' in block and '2.50%' in block and 'DIVIDEND_CASH_CREDITED' in block
    assert '>None<' in block  # x2 of the new run is None, never 0
    assert '2330' in block and '1000.0' in block
    assert html.count('data-testid="compare-row"') == len(report['strategies'])
    html, items = _catalog_items('/strategies')
    h = items[sid]['history']
    assert h['final_state'] == 'NEW_RUN_COMPLETE' and h['accounting'] == 'POSITION_LEDGER_CA_V1' and h['governed_validation'] is True
    assert h['compare'] == {'legacy': 0.1, 'new': 0.125, 'delta': 0.025, 'years': ['2024']}  # the common-years comparison, not window totals
    assert h['usable_years'] == [c['year'] for c in stored['trust'][sid] if c['backtestable']]
    assert 'NEW_RUN_COMPLETE' in html
    detail = client().get(f'/strategies/{sid}/data-trust').get_data(as_text=True)
    assert 'data-testid="history-outcome"' in detail and 'NEW_RUN_COMPLETE' in detail
