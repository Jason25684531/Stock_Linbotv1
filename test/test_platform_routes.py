import collections
from pathlib import Path
import json

import pandas as pd
import pytest

from core.backtest import run_store


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as app_module
    from app import platform_routes
    lab = tmp_path / 'lab'
    (lab / 'items').mkdir(parents=True)
    queue = [{'position': 3, 'item_id': 'LAB:c1', 'kind': 'single', 'conditions': ['c1']},
             {'position': 1, 'item_id': 'LEGACY:mean_reversion', 'kind': 'legacy_baseline', 'strategy_id': 'mean_reversion'}]
    (lab / 'queue.json').write_text(json.dumps({'round': 'r', 'cap': 23, 'queue': queue}), encoding='utf-8')
    pd.DataFrame([{'condition_id': 'c1', 'name': 'n', 'definition': 'd', 'status': 'QUALIFIED', 'reason': '', 'min_coverage': .9}]).to_csv(lab / 'conditions.csv', index=False)
    pd.DataFrame([{'combination_id': 'c1', 'size': 1, 'conditions': 'c1', 'mean_intersection': 10.0, 'min_intersection': 5, 'coverage': 1.0,
                   'mean_pairwise_jaccard': 0.0}]).to_csv(lab / 'combinations.csv', index=False)
    monkeypatch.setattr(platform_routes, 'LAB', lab)
    monkeypatch.setattr(platform_routes, '_failures', {})
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


def _admin(client, monkeypatch, secret='test-secret'):
    from app import platform_routes
    monkeypatch.setenv(platform_routes.ADMIN_ENV, 'tok-123')
    monkeypatch.setattr(platform_routes.app, 'secret_key', secret)
    with client.session_transaction() as s:
        s['csrf'] = 'c'
    return client.post('/admin/login', data={'token': 'tok-123', 'csrf': 'c'})


def _run(group='LAB_ENGINE:x', status='EXPERIMENTAL'):
    record = {'strategy_id': 'mean_reversion', 'strategy_ids': ['mean_reversion'], 'parameter_hash': 'p', 'source_hash': 's', 'data_version': 'd',
              'comparison_group': group, 'certification_status': 'NOT_CERTIFIED', 'status_class': status, 'period': ['2023-01-03', '2025-12-31']}
    nav = pd.DataFrame({'date': ['2025-12-30', '2025-12-31'], 'nav_base': [1.0, 1.1]})
    empty = pd.DataFrame(columns=['stock_id'])
    return run_store.save_run(record, {'nav': nav, 'holdings': empty, 'trades': empty, 'picks': empty,
                                       'metrics': {'metrics': {'sharpe': {'value': None, 'reason': 'NO_TRADES'}}}})


def test_run_pages_and_guards(client):
    a, b, other = _run(), _run(), _run(group='LEGACY_ENGINE:y')
    assert client.get('/backtest/runs').status_code == 200
    page = client.get(f'/backtest/runs/{a}?compare={b}').get_data(as_text=True)
    assert 'NO_TRADES' in page and 'compare-refused' not in page
    assert 'compare-refused' in client.get(f'/backtest/runs/{a}?compare={other}').get_data(as_text=True)
    for bad in ('..%2Flatest', 'latest', '20260101T000000Z-zzzzzzzz'):
        assert client.get(f'/backtest/runs/{bad}').status_code == 404
    (run_store.ROOT / a / 'nav.csv').write_text('date,nav_base\n2025-12-31,9\n', encoding='utf-8')
    assert client.get(f'/backtest/runs/{a}').status_code == 503
    body = client.get('/api/backtest/runs?strategy=mean_reversion').get_json()
    assert body['latest_successful_run'] == other


def test_lab_page_and_run_api_refusals(client, monkeypatch):
    page = client.get('/lab/combinations').get_data(as_text=True)
    assert 'OOS_NOT_AVAILABLE' in page and 'CERTIFIED = NO' in page and 'NOT_RUN' in page
    r = client.post('/api/lab/runs', json={'item_id': 'LAB:c1', 'conditions': ['c1', 'c2']})
    assert r.status_code == 403 and r.get_json()['status'] == 'NOT_REGISTERED'
    assert client.post('/api/lab/runs', json={'item_id': 'LAB:c1'}).status_code == 403  # no admin
    _admin(client, monkeypatch)
    with client.session_transaction() as s:
        token = s['csrf']
    engine_called = []
    from scripts import combination_lab_run as runner
    monkeypatch.setattr(runner, 'run_research_engine_item', lambda *a, **k: engine_called.append(1))
    assert client.post('/api/lab/runs', json={'item_id': 'LAB:c9', 'csrf': token}).get_json()['status'] == 'NOT_REGISTERED'
    assert client.post('/api/lab/runs', json={'item_id': 'LEGACY:mean_reversion', 'csrf': token}).status_code == 403
    from core.research import combination_lab as lab
    monkeypatch.setattr(lab, 'round_trials', lambda *a, **k: lab.ROUND_CAP)
    r = client.post('/api/lab/runs', json={'item_id': 'LAB:c1', 'csrf': token})
    assert r.status_code == 409 and r.get_json()['status'] == 'BUDGET_EXHAUSTED' and not engine_called


def test_admin_blocked_without_token_or_with_default_secret(client, monkeypatch):
    from app import platform_routes
    monkeypatch.delenv(platform_routes.ADMIN_ENV, raising=False)
    page = client.get('/admin/data').get_data(as_text=True)
    assert 'auth-blocked' in page and 'SCHEDULER_SETUP_REQUIRED' in page
    assert client.post('/admin/data', data={'action': 'manual'}).get_json()['status'] == 'AUTH_BLOCKED'
    monkeypatch.setenv(platform_routes.ADMIN_ENV, 'tok-123')
    monkeypatch.setattr(platform_routes.app, 'secret_key', platform_routes.DEFAULT_SECRET)
    assert client.post('/admin/data', data={'action': 'manual'}).get_json()['status'] == 'AUTH_BLOCKED'


def test_admin_login_csrf_rate_limit_and_cookie(client, monkeypatch):
    from app import platform_routes
    response = _admin(client, monkeypatch)
    assert response.status_code == 302
    cookie = response.headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    started = []
    monkeypatch.setattr(platform_routes, '_service', lambda: type('S', (), {'root': None, 'status': lambda self: {'update_in_progress': False},
                                                                             'history': lambda self, n: [], 'run': lambda self, *a, **k: started.append(a)})())
    assert client.post('/admin/data', data={'action': 'manual'}).status_code == 403  # missing CSRF
    assert started == []
    with client.session_transaction() as s:
        csrf = s['csrf']
    assert client.post('/admin/data', data={'action': 'backfill', 'dates': '2099-01-01', 'csrf': csrf}).status_code == 400
    client.post('/admin/logout')
    with client.session_transaction() as s:
        s['csrf'] = 'c'
    for _ in range(5):
        client.post('/admin/login', data={'token': 'wrong', 'csrf': 'c'})
    assert client.post('/admin/login', data={'token': 'tok-123', 'csrf': 'c'}).status_code == 429


def test_latest_selection_disabled_without_stocks(client):
    assert client.get('/selection/latest').status_code == 200
    assert client.get('/api/selection/latest?strategy=nope').status_code == 404
    r = client.get('/api/selection/latest?strategy=mean_reversion')
    body = r.get_json()
    assert r.status_code == 403 and body['status'] == 'LATEST_SIGNAL_DISABLED' and 'ticker' not in body and 'rows' not in body
    assert client.get('/api/selection/latest?strategy=LAB:c1').get_json()['status'] == 'LATEST_SIGNAL_DISABLED'


def test_save_failed_banner_is_rendered(client, monkeypatch):
    import app as app_module
    from app import web_server
    monkeypatch.setattr(web_server, '_build_strategy_catalog', lambda: [{'strategy_id': 'mean_reversion', 'available_for_backtest': True}])
    monkeypatch.setattr(app_module, '_run_portfolio_backtest', lambda *a, **k: ({'metrics': collections.defaultdict(lambda: {'value': None, 'reason': 'n/a'}), 'strategy_performance': {}, 'trades': [],
                                                                                 'save_status': 'SAVE_FAILED', 'save_error': 'OSError'},
                                                                                '2025-01-01', '2025-02-01'))
    monkeypatch.setattr('core.viz_helper.generate_report_from_csv', lambda: {'equity_curve': '{}', 'drawdown': '{}', 'monthly_returns': '{}'})
    page = client.post('/backtest', data={'strategies': 'mean_reversion', 'start_date': '2025-01-01', 'end_date': '2025-02-01'}).get_data(as_text=True)
    assert 'SAVE_FAILED (OSError)' in page
    assert '/backtest/runs/' not in page.split('data-testid="save-status"')[1][:400]


# ---------------------------------------------------------------- change 2026-11-09-autonomous-data-readiness-v1 (design D8)

RECOVERY_V38 = '20261106T173926Z-f0589c22'
REAL_RUNS = Path(__file__).resolve().parents[1] / 'outputs/backtest_runs'  # read-only: these pages never write


def test_recovery_run_page_shows_post_run_evidence(client, monkeypatch):
    monkeypatch.setattr(run_store, 'ROOT', REAL_RUNS)
    if not (run_store.ROOT / RECOVERY_V38).is_dir():
        pytest.skip('recovery run not present')
    html = client.get(f'/backtest/runs/{RECOVERY_V38}').get_data(as_text=True)
    assert 'data-testid="run-evidence"' in html and 'POST_RUN_NOT_ADMISSION_VERIFIED' in html and 'NO_ADMISSION_BLOCK' in html
    assert 'NOT_PINNED' in html and 'trades：PRESENT' in html and 'trades_stress：MISSING' in html and 'data-testid="reproducible">NO<' in html
    listing = client.get('/backtest/runs').get_data(as_text=True)
    assert 'data-testid="run-evidence">POST_RUN_NOT_ADMISSION_VERIFIED<' in listing


def test_isolated_version_2_run_page_is_admission_verified(client, tmp_path, monkeypatch):
    from app import platform_routes
    from scripts import research_workflow as rw
    from test_backtest_run_store import _admitted, _record, _v2, _v2_frames
    rows, admit = rw._trial_rows, rw.admit_experiment  # unpatched: the temporary ledger only
    _, ledger, row, admission, _ = _admitted(tmp_path)
    monkeypatch.setattr(run_store, 'ROOT', tmp_path / 'runs')
    rid = run_store.save_run(_v2(_record(), admission), _v2_frames())
    monkeypatch.setattr(platform_routes, '_ledger', lambda: (rw.verify_trials(ledger) == 'PASS', {r['experiment_id']: r for r in rows(ledger)}))
    html = client.get(f'/backtest/runs/{rid}').get_data(as_text=True)
    assert 'data-testid="evidence-status">ADMISSION_VERIFIED<' in html and 'data-testid="reproducible">YES<' in html
    assert 'trades_stress：PRESENT' in html and 'pit_detail：PRESENT' in html and row['experiment_id'] in html


def test_admin_page_shows_calendar_sources_and_trigger(client, monkeypatch, tmp_path):
    from app import platform_routes
    from services.market_data_update import MarketDataUpdateService
    svc = MarketDataUpdateService(root=tmp_path, adapters={}, today='2026-11-09', calendar=lambda day: 'CALENDAR_UNVERIFIED')
    monkeypatch.setattr(platform_routes, '_service', lambda: svc)
    html = client.get('/admin/data').get_data(as_text=True)
    if 'data-testid="data-calendar"' not in html:  # read-only login view when the admin token is unset
        _admin(client, monkeypatch)
        html = client.get('/admin/data').get_data(as_text=True)
    assert 'data-testid="calendar-state">CALENDAR_UNVERIFIED<' in html and 'data-testid="data-sources"' in html
    assert 'restricted:tpex https://www.tpex.org.tw' in html and 'BENCHMARK_SOURCE_NOT_CROSS_VERIFIED' in html
    assert 'PARTIAL_UNSPLITTABLE' not in html  # monthly revenue is SPLIT since design D4


def test_strategy_center_carries_pit_and_action_evidence(monkeypatch):
    from app import web_server
    monkeypatch.setattr(run_store, 'ROOT', REAL_RUNS)
    if not (web_server.LEGACY_RECOVERY / 'quality_value_low_volatility.json').is_file():
        pytest.skip('recovery evidence not present')
    recovery = web_server._legacy_status('quality_value_low_volatility')['recovery_baseline']
    assert recovery['pit_status'] == 'PIT_UNVERIFIED' and (recovery['pit_checks'], recovery['pit_unverified']) == (41783, 10173)
    assert recovery['pit_detail'] == 'HISTORICAL_PIT_DETAIL_MISSING' and recovery['evidence_status'] == 'POST_RUN_NOT_ADMISSION_VERIFIED'
    expected = 'NOT_RUN' if not web_server.ACTIONS_SUMMARY.is_file() else 'STALE_ACTIONS_PARTIALLY_VERIFIED (stored VALUATION_BLOCKED_ACTIONS_UNKNOWN)'
    assert recovery['actions_diagnosis'] == expected
