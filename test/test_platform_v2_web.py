"""Platform V2 pages on synthetic stored results (no database, no engine, no job process)."""
import json
import re
import hashlib

import pandas as pd
import pytest

from test_intelligence_web import _ctx  # the V1 synthetic run, profile and trust

PUB, BAD = '20200101T000000Z-aaaaaaaa', '20200101T000001Z-bbbbbbbb'
PERF = {k: {'value': 0.1, 'reason': None} for k in ('total_return', 'cagr', 'max_drawdown', 'sharpe')}


@pytest.fixture(autouse=True)
def no_database_in_the_shell(monkeypatch):
    from app import web_server
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda *a, **k: ({'status': 'ok', 'data': {'steps': []}}, 200))
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda *a, **k: None)


@pytest.fixture
def stored(tmp_path, monkeypatch):
    from core.backtest import run_store
    from core.research.reference import batch_job
    from scripts import platform_v2 as p2
    from scripts import strategy_intelligence as si
    from app import platform_v2_routes as routes
    ctx, run = _ctx(tmp_path)
    nav = run['nav']
    out = tmp_path / 'p2'
    for name, value in (('OUT', out), ('RUNS', out / 'runs'), ('PORTFOLIOS', out / 'portfolios'), ('ANALYSIS', out / 'analysis'),
                        ('MODELS', out / 'models'), ('REQUESTS', out / 'requests')):
        monkeypatch.setattr(routes, name, value)
    monkeypatch.setattr(p2, 'RUNS', out / 'runs')
    monkeypatch.setattr(p2, 'ANALYSIS', out / 'analysis')
    monkeypatch.setattr(p2, 'identity', lambda params, snapshot_id: 'RID2-syn')
    params = {'strategy_id': 'syn', 'start': '2016-12-30', 'end': '2020-12-31', 'capital': 1_000_000.0, 'rule': 'LOT_ENGINE_V1',
              'costs': {'fee_rate': 0.001425, 'tax_rate': 0.003, 'minimum_fee': 20.0, 'slippage_max': 0.001}}
    ledger = pd.DataFrame({'date': nav.date, 'cash': nav.nav_base * 0.5, 'positions_value': nav.nav_base * 0.5, 'nav': nav.nav_base, 'holdings': 1})
    positions = pd.concat([pd.DataFrame({'date': nav.date, 'stock_id': '1101', 'shares': 1000, 'value': nav.nav_base * 0.5}),
                           pd.DataFrame({'date': nav.date, 'stock_id': 'CASH', 'shares': None, 'value': nav.nav_base * 0.5})])
    evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': '2020-12-31', 'source_timestamp': '2026-11-12T00:00:00+00:00', 'data_trust': 'PARTIAL',
                'identity': 'RID2-syn', 'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED'], 'strategy_id': 'syn', 'snapshot_id': 'HS1-syn',
                'engine_version': 'ENGINE_V2_FILL_LEDGER_1', 'rule_version': 'LOT_ENGINE_V1'}
    for run_id, ok in ((PUB, True), (BAD, False)):
        record = {'strategy_id': 'syn', 'strategy_ids': ['syn'], 'parameters': params, 'parameter_hash': 'p', 'run_identity': 'RID2-syn', 'source_hash': 's',
                  'data_version': 'HS1-syn', 'snapshot_id': 'HS1-syn', 'comparison_group': 'PLATFORM_V2:x', 'certification_status': 'NOT_CERTIFIED',
                  'status_class': 'EXPERIMENTAL', 'period': ['2016-12-30', '2020-12-31'], 'engine_version': 'ENGINE_V2_FILL_LEDGER_1',
                  'rule_version': 'LOT_ENGINE_V1', 'comparable_with_v1': True, 'accounting': 'POSITION_LEDGER_CA_V1', 'publishable': ok,
                  'not_publishable_reasons': [] if ok else ['base: CASH: ledger 1.00 != engine [2.0] (2017-01-03)'],
                  'reconciliation': {'base': {'status': 'PUBLISHABLE' if ok else 'NOT_PUBLISHABLE', 'first_mismatch': None if ok else '2017-01-03',
                                              'reason': None if ok else 'CASH', 'days_checked': len(nav)}},
                  'evidence': evidence | {'run_id': run_id}}
        frames = {'nav': nav, 'trades': run['trades'], 'trades_stress': run['trades'], 'holdings': positions[positions.stock_id != 'CASH'],
                  'picks': pd.DataFrame(columns=['date', 'stock_id']), 'metrics': {}, 'positions_daily': positions, 'daily_ledger': ledger,
                  'corporate_actions': pd.DataFrame(columns=['date', 'stock_id', 'event_id', 'kind', 'status']), 'open_positions': [],
                  'costs': {'base': {'turnover_annual': 2.0}}, 'fills': pd.DataFrame(columns=['fill_id'])}
        run_store.save_run(record, frames, root=out / 'runs', run_id=run_id)
        p2.analyze(run_id, ctx=ctx, root=out / 'runs')
    code = {name: hashlib.sha256((routes.ROOT / name).read_bytes()).hexdigest()
            for name in ('core/research/platform/prediction.py', 'scripts/platform_v2.py')}
    from core.backtest.run_store import canonical_hash
    model_version = 'PRED1-5d-' + canonical_hash({'horizon': 5, 'spec_hash': 'a' * 64, 'snapshot_id': 'HS1-syn', 'code': code})[:10]
    model_path = out / 'models' / model_version
    model_path.mkdir(parents=True)
    model = {'model_version': model_version, 'horizon': 5, 'status': 'NOT_VALIDATED', 'spec_hash': 'a' * 64, 'snapshot_id': 'HS1-syn',
             'excluded_event_windows': 3, 'evidence': {'evaluation_id': 'MEV1-x'}, 'training': {'folds': []},
             'evaluation': {'horizon': 5, 'rows': 10, 'brier_m1': 0.25, 'brier_b0': 0.25, 'brier_skill': 0.001, 'brier_skill_ci95': [0.0, 0.002],
                            'yearly_skill': {'2019': 0.001}, 'positive_skill_year_share': 1.0, 'calibration': [{'predicted': 0.5, 'observed': 0.5, 'n': 10}],
                            'calibration_slope': 0.4, 'median_abs_error': 0.03, 'coverage_80': 0.78, 'net_top_decile_mean': -0.003,
                            'net_top_decile_periods': 3, 'up_rate_test': 0.49, 'reasons': ['NET_TOP_DECILE -0.00300']}}
    (model_path / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    (model_path / 'predictions.parquet').write_text('must never be read', encoding='utf-8')
    (out / 'governance').mkdir()
    (out / 'governance' / 'LATEST.json').write_text(json.dumps({'checked_at': 'now', 'status': 'DIFFERENT', 'differences': ['h4 file changed: x'],
                                                                 'current': {'effective_trials': 770}}), encoding='utf-8')
    readiness = {'syn': {'state': 'REUSE', 'reason': None, 'data_eligible': True, 'web_runnable': True, 'trusted_years': [2017, 2018],
                         'backtest_window': ['2016-12-30', '2020-12-31']},
                 'blocked': {'state': 'DATA_BLOCKED', 'reason': 'NO_PIT', 'data_eligible': False, 'trusted_years': [], 'backtest_window': None}}
    monkeypatch.setattr('core.research.platform.params.readiness', lambda: readiness)
    monkeypatch.setattr(batch_job, 'start', lambda **k: pytest.fail('a GET or a refused POST started a job'))
    monkeypatch.setattr(batch_job, 'state', lambda: {'status': 'IDLE', 'pids': []})
    from services import smart_selection
    monkeypatch.setattr(smart_selection, 'load', lambda app: {'status': 'GOVERNANCE_BLOCKED', 'reason': 'EXISTING_WEB_AUTHORIZATION_NOT_VERIFIED',
                                                               'rows': [], 'consensus': [], 'evidence': {}})
    return {'out': out, 'ctx': ctx, 'model_path': model_path / 'model.json',
            'analysis': json.loads(next((out / 'analysis').glob(f'{PUB}__*.json')).read_text(encoding='utf-8'))['result']}


def client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


def test_unreviewed_model_report_is_not_served_as_current(stored):
    path = stored['out'] / 'MODEL_VALIDATION_REPORT.md'
    path.write_text('DEV_WINDOW_SKILL', encoding='utf-8')
    response = client().get('/platform/reports/MODEL_VALIDATION_REPORT.md')
    assert response.status_code == 409
    assert b'VALIDATION_EVIDENCE_MISSING' in response.data


def approve_run(out, kind, run_id):
    from app import platform_v2_routes as routes
    folder = out / ('portfolios' if kind == 'portfolio' else 'runs') / run_id
    paths = {'run.json': folder / 'run.json', 'manifest.json': folder / 'manifest.json',
             'engine_v2.py': routes.ROOT / 'core/research/platform/engine_v2.py',
             'ledger_v2.py': routes.ROOT / 'core/research/platform/ledger_v2.py',
             'platform_v2_engine.py': routes.ROOT / 'scripts/platform_v2_engine.py',
             'platform_v2.py': routes.ROOT / 'scripts/platform_v2.py'}
    if kind == 'portfolio':
        paths['portfolio_v2.py'] = routes.ROOT / 'core/research/platform/portfolio_v2.py'
    marker = out / 'acceptance' / 'publication' / kind / f'{run_id}.json'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'status': 'PASS', 'identity': run_id,
                                 'sha256': {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}}), encoding='utf-8')


def test_five_entries_respond_and_get_starts_nothing(stored):
    c = client()
    for path in ('/platform', '/platform/selection', '/platform/predictions', '/platform/backtest', '/platform/portfolio', '/platform/system'):
        response = c.get(path)
        assert response.status_code == 200, path
    hub = c.get('/platform').get_data(as_text=True)
    assert all(f'entry-{n}' in hub for n in ('selection', 'predictions', 'backtest', 'portfolio', 'system'))


def test_report_numbers_equal_the_stored_result(stored):
    approve_run(stored['out'], 'backtest', PUB)
    page = client().get(f'/platform/backtest/{PUB}').get_data(as_text=True)
    prov = stored['analysis']['performance']['provisional']
    shown = lambda testid: re.search(rf'data-testid="{testid}">.*?<span class="num">([^<]+)</span>', page, re.S)[1]
    assert shown('kpi-cagr') == '%.1f%%' % (prov['cagr']['value'] * 100)
    assert shown('kpi-total') == '%.1f%%' % (prov['total_return']['value'] * 100)
    assert shown('kpi-mdd') == '%.1f%%' % (prov['max_drawdown']['value'] * 100)
    month = stored['analysis']['monthly']['2017']['months']['3']['value']
    assert re.search(r'data-testid="month-2017-3"[^>]*>([^<]+)<', page)[1] == '%+.1f%%' % (month * 100)
    assert 'data-testid="month-2016-1"' in page and re.search(r'data-testid="month-2016-1">([^<]+)<', page)[1] == '無交易日'
    assert 'NO_TRADES' not in page and 'fills.csv' in page


def test_unverifiable_run_shows_no_performance_and_states_are_distinct(stored):
    page = client().get(f'/platform/backtest/{BAD}').get_data(as_text=True)
    assert 'data-testid="not-publishable"' in page and 'data-testid="kpi"' not in page and 'month-' not in page
    for state in ('NO_TRADES', 'DATA_MISSING', 'NOT_PUBLISHABLE'):
        assert state in (open('templates/platform_run.html', encoding='utf-8').read())
    assert client().get('/platform/backtest/20200101T000000Z-cccccccc').status_code == 404
    assert client().get(f'/platform/backtest/{PUB}/files/../run.json').status_code == 404


def test_guest_and_missing_csrf_are_refused(stored, monkeypatch):
    c = client()
    for path in ('/platform/backtest', '/platform/portfolio', '/platform/system/pipeline', '/platform/system/cancel', '/platform/system/retry'):
        assert c.post(path, data={}).status_code == 403, path
    for path in ('/platform/backtest', '/platform/portfolio', '/platform/system'):
        page = c.get(path).get_data(as_text=True)
        assert 'name="csrf"' not in page, path  # guests see no admin control
    from app import platform_routes
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    monkeypatch.setattr(platform_routes, 'admin_state', lambda: (True, ''))
    assert c.post('/platform/backtest', data={'csrf': 'nope'}).status_code == 403


def test_admin_submit_validates_and_uses_the_single_slot(stored, monkeypatch):
    from app import platform_routes
    from core.research.reference import batch_job
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    monkeypatch.setattr(platform_routes, 'admin_state', lambda: (True, ''))
    calls = []
    monkeypatch.setattr(batch_job, 'start', lambda **k: calls.append(k) or {'status': 'STARTED', 'pid': 1})
    c = client()
    with c.session_transaction() as s:
        s['csrf'] = 'tok'
    form = {'csrf': 'tok', 'strategy_id': 'syn', 'start': '2017-01-03', 'end': '2019-12-31', 'capital': '2000000', 'fee_rate': '0.001425',
            'tax_rate': '0.003', 'minimum_fee': '20', 'slippage_max': '0.001', 'rule': 'LOT_BOARD_1000'}
    assert c.post('/platform/backtest', data=form | {'end': '2026-01-05'}).status_code == 400 and not calls
    assert c.post('/platform/backtest', data=form | {'strategy_id': 'blocked'}).status_code == 400 and not calls
    assert c.post('/platform/backtest', data=form).status_code == 302
    request_file = calls[0]['args'][1]
    assert calls[0]['kind'] == 'platform_backtest' and json.loads(open(request_file, encoding='utf-8').read())['capital'] == 2_000_000.0
    monkeypatch.setattr(batch_job, 'start', lambda **k: {'status': 'RUN_IN_PROGRESS', 'pids': [9]})
    busy = c.post('/platform/backtest', data=form)
    assert busy.status_code == 409 and busy.get_json()['pids'] == [9]
    assert 'backtest-form' in c.get('/platform/backtest').get_data(as_text=True)


def test_portfolio_page_only_offers_web_runnable_members(stored, monkeypatch):
    from app import platform_v2_routes as routes
    captured = {}
    monkeypatch.setattr(routes, '_readiness', lambda: {
        'ready': {'trusted_years': [2022], 'web_runnable': True},
        'blocked': {'trusted_years': [2022], 'web_runnable': False},
    })
    monkeypatch.setattr(routes, 'render_template', lambda _name, **context: captured.update(context) or 'ok')

    assert client().get('/platform/portfolio').status_code == 200
    assert list(captured['eligible']) == ['ready']


def test_portfolio_submit_rejects_member_without_publishable_web_run(stored, monkeypatch):
    from app import platform_routes
    from core.research.platform import params
    monkeypatch.setattr(platform_routes, '_require_admin_write', lambda: None)
    monkeypatch.setattr(params, 'readiness', lambda: {
        'ready': {'trusted_years': [2022], 'web_runnable': True, 'backtest_window': ['2020-01-02', '2025-12-31']},
        'blocked': {'trusted_years': [2022], 'web_runnable': False, 'backtest_window': ['2020-01-02', '2025-12-31']},
    })

    response = client().post('/platform/portfolio', data={'members': ['ready', 'blocked'], 'weights': '0.5,0.5'})
    assert response.status_code == 400
    assert response.get_json()['reason'] == 'MEMBER_NOT_WEB_RUNNABLE'


def test_not_validated_model_shows_no_stock_prediction(stored):
    page = client().get('/platform/predictions?stock=2330').get_data(as_text=True)
    assert 'data-testid="not-validated"' in page and 'data-testid="today-blocked"' in page and 'GOVERNANCE_BLOCKED' in page
    assert 'history-5' not in page  # the parquet is never read for a NOT_VALIDATED model


def test_stale_model_code_identity_is_hidden(stored):
    model = json.loads(stored['model_path'].read_text(encoding='utf-8'))
    model['model_version'] = 'PRED1-5d-0123456789'
    stored['model_path'].write_text(json.dumps(model), encoding='utf-8')
    page = client().get('/platform/predictions').get_data(as_text=True)
    assert 'data-testid="stale-models"' in page and 'MODEL_STALE' in page
    assert 'data-testid="model-5"' not in page


def test_unreviewed_saved_run_never_displays_performance(stored):
    page = client().get(f'/platform/backtest/{PUB}').get_data(as_text=True)
    assert 'VALIDATION_EVIDENCE_MISSING' in page and 'data-testid="kpi"' not in page


def test_review_marker_must_match_artifacts(stored):
    from app import platform_v2_routes as routes
    approve_run(stored['out'], 'backtest', PUB)
    assert routes._run_reviewed(stored['out'] / 'runs', PUB)
    marker = stored['out'] / 'acceptance' / 'publication' / 'backtest' / f'{PUB}.json'
    data = json.loads(marker.read_text(encoding='utf-8'))
    data['sha256']['run.json'] = '0' * 64
    marker.write_text(json.dumps(data), encoding='utf-8')
    assert not routes._run_reviewed(stored['out'] / 'runs', PUB)


def test_unreviewed_model_never_displays_stock_predictions(stored):
    path = stored['model_path']
    model = json.loads(path.read_text(encoding='utf-8'))
    model['status'] = 'DEV_WINDOW_SKILL'
    path.write_text(json.dumps(model), encoding='utf-8')
    page = client().get('/platform/predictions?stock=2330').get_data(as_text=True)
    assert 'VALIDATION_EVIDENCE_MISSING' in page and 'history-5' not in page


def test_undefined_model_metrics_are_explained(stored):
    path = stored['model_path']
    model = json.loads(path.read_text(encoding='utf-8'))
    model['evaluation'].update(brier_skill=None, brier_skill_ci95=[None, None], yearly_skill={'2019': None},
                               undefined_reasons={'brier_skill': 'ZERO_BASELINE_BRIER', 'brier_skill_ci95': 'ZERO_BASELINE_BRIER',
                                                  'yearly_skill': {'2019': 'ZERO_BASELINE_BRIER'}})
    path.write_text(json.dumps(model), encoding='utf-8')
    page = client().get('/platform/predictions').get_data(as_text=True)
    assert 'ZERO_BASELINE_BRIER' in page and '不可計算' in page


def test_selection_fails_closed_when_authorization_unverified(stored):
    page = client().get('/platform/selection').get_data(as_text=True)
    assert 'selection-blocked' in page and 'candidates' not in page


def test_system_lists_governance_differences(stored):
    page = client().get('/platform/system').get_data(as_text=True)
    assert 'governance-differences' in page and 'h4 file changed' in page


def test_portfolio_report_labels_demo_and_industry(stored):
    from core.backtest import run_store
    record = {'strategy_id': 'PORTFOLIO:syn', 'strategy_ids': ['syn'], 'parameters': {'members': ['syn'], 'weights': [1.0], 'capital': 1e6, 'rebalance': 'REBAL_NONE'},
              'parameter_hash': 'p', 'run_identity': 'PID2-x', 'source_hash': 's', 'data_version': 'HS1-syn', 'comparison_group': 'PLATFORM_V2:PORTFOLIO',
              'certification_status': 'NOT_CERTIFIED', 'status_class': 'EXPERIMENTAL', 'period': ['2017-01-03', '2020-12-31'], 'publishable': True,
              'not_publishable_reasons': [], 'rules': {'allocation': 'ALLOC_V1', 'contention': 'CONTEND_V1', 'rebalance': 'REBAL_NONE', 'lot': 'LOT_ENGINE_V1'},
              'reconciliation': {'base': {'status': 'PUBLISHABLE', 'days_checked': 2}},
              'analysis': {'portfolio': {'base': PERF, 'stress': PERF}, 'members': {'syn': PERF}, 'benchmark_total_return': {'total_return': 0.1, 'reason': None},
                           'correlation': None, 'overlap': {'standalone_overlap_days': 0, 'sessions': 2, 'note': ''},
                           'common_large_losses': {'count': 0, 'rule': 'r'}, 'contention': {'CASH_CONTENTION': 2}, 'costs': {'fees_and_tax_paid': 1, 'traded_value': 2, 'turnover_annual': 1},
                           'industry_concentration': {'value': None, 'reason': 'NOT_AVAILABLE: the snapshot has no industry classification'}}}
    frames = {'nav': pd.DataFrame({'date': ['2017-01-03'], 'nav_base': [1.0], 'nav_stress': [1.0]}), 'trades': pd.DataFrame(columns=['stock_id']),
              'holdings': pd.DataFrame(columns=['date']), 'picks': pd.DataFrame(columns=['date']), 'metrics': {}}
    run_id = run_store.save_run(record, frames, root=stored['out'] / 'portfolios', run_id='20200101T000002Z-dddddddd')
    approve_run(stored['out'], 'portfolio', run_id)
    page = client().get(f'/platform/portfolio/{run_id}').get_data(as_text=True)
    assert 'NOT_AVAILABLE' in page and '不是策略' in page and 'CASH_CONTENTION' in page
