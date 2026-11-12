"""End to end on synthetic data: run_job writes results, the web pages read them. No real run, snapshot or process."""
import json

import numpy as np
import pandas as pd
import pytest

from scripts import strategy_intelligence as si

YEARS = range(2017, 2021)


def _ctx(tmp_path):
    days = pd.bdate_range('2016-12-30', '2020-12-31')
    rng = np.random.default_rng(3)
    nav = 1_000_000 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(days)))
    nav[0] = 1_000_000
    bench = pd.DataFrame({'date': days, 'taiex_price': 100 * np.cumprod(1 + rng.normal(0.0002, 0.01, len(days))),
                          'taiex_total_return': 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days)))})
    bars = pd.DataFrame({'trade_date': days, 'stock_id': '1101', 'name': 'x', 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.0,
                         'volume': 1e6, 'amount': 1e7, 'status': 'TRADED'})
    trades = pd.DataFrame({'stock_id': ['1101'] * 40, 'buy_date': [d.strftime('%Y-%m-%d') for d in days[5:805:20]],
                           'sell_date': [d.strftime('%Y-%m-%d') for d in days[10:810:20]], 'buy_price': 10.0, 'sell_price': 10.2, 'shares': 1000,
                           'profit_pct': [2.0 if i % 3 else -1.0 for i in range(40)], 'reason': 'x'})
    run = {'run_id': '20200101T000000Z-aaaaaaaa', 'manifest_sha256': 'm' * 64, 'nav': pd.DataFrame({'date': days.strftime('%Y-%m-%d'), 'nav_base': nav, 'nav_stress': nav * 0.99}),
           'trades': trades, 'actions': None,
           'record': {'run_identity': 'RID1:abc', 'snapshot_id': 'HS1-syn', 'accounting': 'POSITION_LEDGER_CA_V1', 'ledger_version': 'f' * 64,
                      'period': ['2016-12-30', '2020-12-31'], 'comparison_group': 'REFERENCE_HISTORY:x', 'parameters': {'execution_contract': 'next_session_open_v2'},
                      'cost_model': {'fee_rate': 0.001425, 'tax_rate': 0.003, 'minimum_fee': 20, 'slippage_max': 0.001, 'stress_multiple': 2},
                      'open_positions': [], 'cost_summary': {'slippage': 'inside fill prices'}}}
    prof = json.loads((si.PROFILE).read_text(encoding='utf-8'))
    prof['strategies'] = {'syn': {'primary_type': 'trend_momentum', 'secondary_types': [], 'evidence': 'test'},
                          'blocked': {'primary_type': 'fundamental', 'secondary_types': [], 'evidence': 'test'}}
    trust = {'syn': [{'year': 2016, 'status': 'PARTIAL'}] + [{'year': y, 'status': 'VERIFIED'} for y in YEARS],
             'blocked': [{'year': y, 'status': 'ACCESS_BLOCKED'} for y in YEARS]}
    ctx = {'evaluation': {'snapshot_id': 'HS1-syn', 'evaluation_id': 'EV1-syn', 'trust': trust}, 'bars': bars, 'bench': bench, 'sessions': pd.Series(days),
           'plan': {'syn': {'state': 'REUSE', 'run_id': run['run_id'], 'family': 'tquant'}, 'blocked': {'state': 'DATA_BLOCKED', 'reason': 'NO_PIT', 'family': 'native'}},
           'profile': prof, 'profile_id': 'EP1-syn', 'regime_spec_id': 'RS1-syn', 'settings': {}, 'code': {'x.py': '0'}, 'engine_files': {}}
    return ctx, run


@pytest.fixture
def stored(tmp_path, monkeypatch):
    ctx, run = _ctx(tmp_path)
    cards = tmp_path / 'cards'
    cards.mkdir()
    for sid in ('syn', 'blocked'):
        (cards / f'{sid}.json').write_text(json.dumps({'strategy_id': sid, 'fields': {'hypothesis': {'text': 'buy strength', 'sources': [
            {'ref': 'core/backtest/runner.py::MAX_HOLDINGS'}]}}}), encoding='utf-8')
    out = tmp_path / 'si'
    for name, value in (('OUT', out), ('RESULTS', out / 'results'), ('CARDS', cards)):
        monkeypatch.setattr(si, name, value)
    monkeypatch.setattr(si, 'catalog_ids', lambda: ['syn', 'blocked'])
    monkeypatch.setattr(si, 'load_context', lambda: ctx)
    monkeypatch.setattr(si, 'load_run', lambda run_id: run)
    monkeypatch.setattr(si, 'risk_numbers', lambda sid, settings: {'max_holdings': 3})
    monkeypatch.setattr(si, 'code_hashes', lambda: ctx['code'])  # the synthetic job's code is the code on disk for tables()
    assert si.run_job('jweb') == 0
    from app import intelligence_routes as ir
    monkeypatch.setattr(ir, 'OUT', out)
    monkeypatch.setattr(ir, 'RESULTS', out / 'results')
    monkeypatch.setattr(ir, '_catalog', lambda: ['syn', 'blocked'])
    monkeypatch.setattr('core.research.reference.batch_job.state', lambda: {'status': 'IDLE'})
    return out


def _client():
    from app import app
    return app.test_client()


def test_pages_show_stored_numbers_and_provenance(stored):
    latest = json.loads((stored / 'LATEST.json').read_text())
    result = json.loads((stored / 'results' / latest['strategies']['syn'] / 'syn.json').read_text())
    page = _client().get('/intelligence/syn').get_data(as_text=True)
    cagr = result['performance']['verified']['cagr']['value']
    assert f'{cagr * 100:.1f}%' in page
    for token in (result['run']['run_id'], result['run']['run_identity'], 'HS1-syn', 'EV1-syn', result['identity'], 'POSITION_LEDGER_CA_V1'):
        assert token in page
    assert '歷史情境模擬，非未來價格或投資報酬預測' in page
    assert result['scenarios']['status'] == 'SUFFICIENT' and result['scenarios']['check']['span'] == [2017, 2020]
    official_2017_2020 = len(pd.bdate_range('2017-01-01', '2020-12-31'))
    assert result['scenarios']['simulation']['sample_sessions'] == official_2017_2020  # the PARTIAL 2016 session is never sampled
    assert 'score' not in json.dumps(result['dimensions'])
    index = _client().get('/intelligence').get_data(as_text=True)
    assert index.index('row-syn') < index.index('row-blocked')


def test_report_tables_come_from_the_stored_results(stored, monkeypatch):
    from scripts import intelligence_report as rep
    monkeypatch.setattr(rep, 'OUT', stored)
    path = rep.tables()
    text = path.read_text(encoding='utf-8')
    latest = json.loads((stored / 'LATEST.json').read_text())
    result = json.loads((stored / 'results' / latest['strategies']['syn'] / 'syn.json').read_text())
    assert latest['job_id'] in text and result['identity'] in text
    assert f"{result['performance']['verified']['cagr']['value']:.1%}" in text
    assert '| blocked |' in text and 'NO_PIT' in text
    assert _client().get('/intelligence/reports/REPORT_TABLES.md').status_code == 200


def test_final_report_has_every_section_per_strategy(stored, monkeypatch):
    from scripts import intelligence_report as rep
    monkeypatch.setattr(rep, 'OUT', stored)
    monkeypatch.setattr(rep, 'ACC', stored / 'acceptance')
    text = rep.build().read_text(encoding='utf-8')
    for heading in ('**1. 年化淨報酬目標**', '**2. 最大回撤與回本時間', '**3. 市場環境**', '**4. 未來一年歷史情境**', '**5. 交易可行性與組合分散**', '**7. 實作與說明差異**'):
        assert heading in text
    result = json.loads((stored / 'results' / json.loads((stored / 'LATEST.json').read_text())['strategies']['syn'] / 'syn.json').read_text())
    assert result['run']['run_id'] in text and result['identity'] in text and 'HS1-syn' in text
    assert '`blocked`' in text and 'NO_PIT' in text and '未執行' in text  # missing acceptance evidence is stated, never assumed


def test_an_older_result_is_marked_superseded(stored):
    latest = json.loads((stored / 'LATEST.json').read_text())
    current = latest['strategies']['syn']
    older = 'SI1-' + 'a' * 16
    data = json.loads((stored / 'results' / current / 'syn.json').read_text())
    si.write_json(stored / 'results' / older / 'syn.json', data | {'identity': older})
    assert 'superseded-warning' in _client().get(f'/intelligence/syn?result={older}').get_data(as_text=True)
    assert 'superseded-warning' not in _client().get('/intelligence/syn').get_data(as_text=True)
    assert _client().get(f'/intelligence/syn/evidence.json?result={older}').status_code == 200


def test_blocked_strategy_has_a_card_and_no_numbers(stored):
    page = _client().get('/intelligence/blocked').get_data(as_text=True)
    assert 'NO_PIT' in page and 'card-field-hypothesis' in page and 'nav-chart' not in page
    assert 'buy strength' in page


def test_unknown_strategy_identity_and_report_names(stored):
    c = _client()
    assert c.get('/intelligence/nope').status_code == 404
    assert c.get('/intelligence/syn?result=SI1-0000000000000000').status_code == 404
    assert c.get('/intelligence/syn?result=../../etc').status_code == 404
    assert c.get('/intelligence/reports/..%2F..%2Fapp.py').status_code == 404
    assert c.get('/intelligence/reports/STRATEGY_INTELLIGENCE_FINAL_ACCEPTANCE_REPORT.md').status_code == 404
    assert c.get('/intelligence/syn/evidence.json').status_code == 200


def test_get_never_starts_a_job_and_guests_cannot_post(stored, monkeypatch):
    from core.research.reference import batch_job
    monkeypatch.setattr(batch_job, 'start', lambda **k: pytest.fail('started from a GET or by a guest'))
    c = _client()
    for path in ('/intelligence', '/intelligence/syn', '/intelligence/portfolio', '/intelligence/forward'):
        assert c.get(path).status_code == 200
    assert c.post('/admin/intelligence/start').status_code == 403
    assert 'admin-controls' not in c.get('/intelligence').get_data(as_text=True)
    forward = c.get('/intelligence/forward').get_data(as_text=True)
    assert 'GOVERNANCE_BLOCKED' in forward and 'ticker' not in forward and 'stock_id' not in forward


def test_admin_start_needs_csrf_and_cancel_only_targets_intelligence(stored, monkeypatch):
    from app import platform_routes
    from core.research.reference import batch_job
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    monkeypatch.setattr(platform_routes, 'admin_state', lambda: (True, ''))
    calls = []
    monkeypatch.setattr(batch_job, 'start', lambda **k: calls.append(k) or {'status': 'STARTED', 'pid': 1})
    monkeypatch.setattr(batch_job, 'cancel', lambda: pytest.fail('cancelled a non-intelligence job'))
    monkeypatch.setattr(batch_job, 'state', lambda: {'status': 'RUNNING', 'kind': 'history_run'})
    c = _client()
    with c.session_transaction() as s:
        s['csrf'] = 'tok'
    assert c.post('/admin/intelligence/start', data={'csrf': 'bad'}).status_code == 403 and not calls
    assert c.post('/admin/intelligence/start', data={'csrf': 'tok'}).status_code == 302 and calls == [{'workers': 1, 'kind': 'intelligence'}]
    assert c.post('/admin/intelligence/cancel', data={'csrf': 'tok'}).status_code == 409
    assert 'admin-controls' in c.get('/intelligence').get_data(as_text=True)


def test_stale_identity_shows_no_verdict_or_probability(tmp_path, monkeypatch):
    ctx, run = _ctx(tmp_path)
    ctx['plan']['syn'] = {'state': 'RUN', 'run_id': None, 'family': 'tquant'}
    monkeypatch.setattr(si, 'pick_run', lambda sid, row, snapshot_id=None: (run['run_id'], 'IDENTITY_STALE') if row['state'] == 'RUN' else (None, row['state']))
    cards = tmp_path / 'cards'
    cards.mkdir()
    for sid in ('syn', 'blocked'):
        (cards / f'{sid}.json').write_text(json.dumps({'strategy_id': sid, 'fields': {}}), encoding='utf-8')
    for name, value in (('OUT', tmp_path / 'si'), ('RESULTS', tmp_path / 'si' / 'results'), ('CARDS', cards)):
        monkeypatch.setattr(si, name, value)
    monkeypatch.setattr(si, 'catalog_ids', lambda: ['syn', 'blocked'])
    monkeypatch.setattr(si, 'load_context', lambda: ctx)
    monkeypatch.setattr(si, 'load_run', lambda run_id: run)
    monkeypatch.setattr(si, 'risk_numbers', lambda sid, settings: {})
    monkeypatch.setattr(si, 'code_hashes', lambda: ctx['code'])
    si.run_job('jstale')
    latest = json.loads((tmp_path / 'si' / 'LATEST.json').read_text())
    r = json.loads((tmp_path / 'si' / 'results' / latest['strategies']['syn'] / 'syn.json').read_text())
    assert r['status'] == 'PROVISIONAL_IDENTITY_STALE' and r['performance']['verified'] is None and r['performance']['verified_span'] is None
    assert set(r['year_status'].values()) == {'IDENTITY_STALE'} and all(v.get('label') != 'VERIFIED' for v in r['performance']['ranges'].values())
    regimes = r['performance']['regime']['regimes']
    assert all(g['sessions'] == 0 for g in regimes['VERIFIED_ONLY'].values()) and sum(g['sessions'] for g in regimes['VERIFIED_AND_PARTIAL'].values()) > 0
    assert r['dimensions']['return_target']['status'] == 'INSUFFICIENT_VERIFIED_HISTORY'
    assert r['scenarios']['status'] == 'IDENTITY_STALE' and 'simulation' not in r['scenarios']
    assert all(i['status'] != 'GOOD' for items in r['health'].values() for i in items)
