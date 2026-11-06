"""h4-web-integration-v1: research snapshot parity, read-only research APIs, Strategy Center and Backtest integration,
latest-data provider checks, and no production path."""
import ast
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from app import research_payloads as rp
from core.research import h4_signal, latest_signal, strategy_signal as ss
from scripts import export_h4_web_snapshot as export

ROOT = Path(__file__).resolve().parents[1]
H4 = h4_signal.STRATEGY_ID
API = f'/api/research/strategies/{H4}'
needs_snapshot = pytest.mark.skipif(not (rp.SNAPSHOT / 'manifest.json').is_file() or not export.SCORES.is_file(),
                                    reason='H4 web snapshot or archive not present')


@pytest.fixture(scope='module')
def client():
    from app import app
    return app.test_client()


# --- snapshot (design D2) -------------------------------------------------------------------------------------------

@needs_snapshot
def test_snapshot_signals_match_the_archive_on_all_twelve_days():
    scores = pd.DataFrame(json.loads(export.SCORES.read_text(encoding='utf-8')))
    targets = pd.read_csv(export.RUN / 'targets.csv', dtype={'asset_id': str}, encoding='utf-8-sig')
    days = rp.snapshot()['signal_dates']
    assert days == sorted(targets['asof_date'].astype(str).str[:10].unique()) and len(days) == 12
    assert {'2025-09-26', '2025-12-26'} <= set(days)
    for day in days:
        signals = pd.read_csv(rp.SNAPSHOT / 'signals' / f'{day}.csv', dtype={'ticker': str})
        scored = signals.loc[signals['eligibility_status'].isin(['ELIGIBLE', 'EVENT_EXCLUDED'])].set_index('ticker')['final_score'].sort_index()
        expected = scores.loc[scores['asof_date'] == day].set_index('asset_id')['composite_score'].sort_index()
        assert list(scored.index) == list(expected.index), day
        np.testing.assert_allclose(scored, expected, rtol=1e-9, atol=1e-12)
        chosen = signals.loc[signals['target_weight'] > 0].sort_values('ticker')
        want = targets.loc[targets['asof_date'].astype(str).str[:10] == day].sort_values('asset_id')
        assert list(chosen['ticker']) == list(want['asset_id']), day
        np.testing.assert_allclose(chosen['target_weight'], want['target_weight'], rtol=1e-9, atol=1e-12)


def test_export_refuses_a_tampered_archive_and_outside_outputs(tmp_path, monkeypatch):
    fake = tmp_path / 'summary.json'
    fake.write_text(json.dumps({'run_outputs_sha256': {'H4': {'stress.json': '0' * 64}}}), encoding='utf-8')
    monkeypatch.setattr(export, 'ATTRIBUTION', fake)
    with pytest.raises(ValueError, match='ARCHIVE_SHA_MISMATCH:stress.json'):
        export.performance()
    with pytest.raises(ss.ResearchOnlyViolation, match='OUTPUT_OUTSIDE_RESEARCH_DIRECTORY'):
        export.write({}, [], {}, {}, output=tmp_path / 'elsewhere')
    assert not (tmp_path / 'elsewhere').exists()


@needs_snapshot
def test_altered_snapshot_is_data_not_available(tmp_path, monkeypatch, client):
    copy = tmp_path / 'h4_web_v1'
    shutil.copytree(rp.SNAPSHOT, copy)
    (copy / 'signals/2025-12-26.csv').write_text('ticker\n9999\n', encoding='utf-8')
    monkeypatch.setattr(rp, 'SNAPSHOT', copy)
    response = client.get(f'{API}/signals/2025-12-26')
    assert response.status_code == 503 and response.get_json()['code'] == 'DATA_NOT_AVAILABLE'
    assert client.get(f'{API}/backtest?period=FULL').status_code == 503


# --- read-only APIs (design D4, D5) ---------------------------------------------------------------------------------

@pytest.mark.parametrize('path, status, code', [
    ('/signals/2026-01-05', 403, 'SEALED'), ('/signals/2016-01-04', 409, 'DATA_BLOCKED'), ('/signals/20251226', 400, 'INVALID_DATE'),
    ('/backtest?period=2026', 403, 'SEALED'), ('/backtest?period=2016-2022', 409, 'DATA_BLOCKED'),
    ('/backtest?start_date=2023-01-03&end_date=2026-03-31', 403, 'SEALED'),
    ('/backtest?start_date=2023-02-01&end_date=2023-03-01', 400, 'PERIOD_NOT_ARCHIVED'), ('/backtest?period=FULL&initial_capital=-5', 400, 'INVALID_CAPITAL')])
def test_guards_answer_before_any_file_is_read(client, monkeypatch, path, status, code):
    monkeypatch.setattr(rp, 'snapshot', lambda: pytest.fail('snapshot read before the guard'))
    response = client.get(API + path)
    assert response.status_code == status and response.get_json()['code'] == code


def test_unknown_ids_traversal_and_writes_are_refused(client):
    assert client.get('/api/research/strategies/hybrid_trend_rank/signals/2025-12-26').status_code == 404
    assert client.get(f'{API}/signals/..%2F..%2Fconfig').status_code in (400, 404)
    assert client.get(f'{API}/signals/2025-01-0%2F').status_code in (400, 404)
    for url in ('/api/research/strategies', f'{API}/signals/2025-12-26', f'{API}/backtest', f'{API}/latest'):
        assert client.post(url).status_code == 405


@needs_snapshot
def test_signal_payload_has_top10_changes_and_value_based_reasons(client):
    body = client.get(f'{API}/signals/2025-12-26').get_json()
    assert body['previous_signal_date'] == '2025-09-26' and body['research_only'] is True
    assert [r['rank'] for r in body['top']] == list(range(1, 11)) and all(r['target_weight'] == .1 for r in body['top'])
    assert {'BUY', 'SELL'} <= {c['action'] for c in body['changes']}
    first = body['top'][0]
    assert f"{first['g2_score']:.2f}" in first['reason'] and f"{first['final_score']:.4f}" in first['reason'] and '排名第 1' in first['reason']
    assert set(body['rows'][0]) >= {'ticker', 'signal_date', 'g2_score', 'g3_score', 'momentum_score', 'final_score', 'rank',
                                    'target_weight', 'eligibility_status', 'data_version', 'source_hash'}


def test_reason_template_uses_only_row_values():
    row = {'eligibility_status': 'ELIGIBLE', 'g2_score': .91, 'g3_score': .5, 'momentum_score': .1, 'final_score': .6012, 'rank': 7, 'target_weight': .1}
    assert rp.reason(row) == '營業利益成長分數 0.91（較高）、EPS 成長分數 0.50（中等）、股價動能分數 0.10（較低），綜合分數 0.6012，排名第 7，進入 Top 10。'
    assert rp.reason({'eligibility_status': 'NO_GROWTH'}) == rp.STATUS_REASONS['NO_GROWTH']


# --- Strategy Center (design D1) ------------------------------------------------------------------------------------

@needs_snapshot
def test_strategy_center_shows_h4_as_uncertified_research(client):
    entry = rp.catalog_entries()[0]
    assert (entry['strategy_id'], entry['lifecycle'], entry['eligibility'], entry['category']) == (H4, 'RESEARCH', 'RESEARCH_UNCERTIFIED', 'Research')
    assert entry['available_for_daily'] is False and entry['validation']['research_advice'] == 'REVISE'
    assert entry['validation']['registration_decision'] == 'CONTINUE'
    assert entry['validation']['gates'] == {'r1': 'PASS', 'r2': 'FAIL', 'r3': 'FAIL', 'r4': 'FAIL', 'r5': 'PASS'}
    assert [p['status'] for p in entry['validation']['periods']] == ['ARCHIVED', 'DATA_BLOCKED', 'SEALED']
    page = client.get('/strategies').get_data(as_text=True)
    assert H4 in page and "key: 'Research'" in page


def test_runtime_catalog_and_daily_selection_never_contain_h4(client, monkeypatch):
    from app import services
    from app.web_server import _build_strategy_catalog
    from core.strategy_manager import StrategyManager
    assert H4 not in {item['strategy_id'] for item in _build_strategy_catalog()}
    assert H4 not in StrategyManager().list_strategies()
    before = list(services.strategy_manager.get_active_strategy_names())
    # patch the class: patching the services proxy would leave an instance attribute behind after undo
    monkeypatch.setattr(StrategyManager, 'set_active_strategies', lambda *_: pytest.fail('research id reached the Daily setter'))
    for strategies in ([H4], ['hybrid_trend_rank', H4.lower()]):
        response = client.post('/update_strategy', data={'strategies': strategies})
        assert response.status_code == 302
    assert list(services.strategy_manager.get_active_strategy_names()) == before


# --- detail page (design D4) ----------------------------------------------------------------------------------------

@needs_snapshot
def test_detail_page_renders_configuration_and_snapshot_status(client, tmp_path, monkeypatch):
    page = client.get(f'/strategies/research?id={H4}').get_data(as_text=True)
    for text in ('G2 營業利益 YoY', '25%', '動能 near_high_252d', '50%', 'Top 10', 'REVISE', 'DATA_BLOCKED', 'SEALED', 'LATEST_SIGNAL_DISABLED', '查看封存回測報告'):
        assert text in page, text
    assert client.get('/strategies/research?id=H5:NONE').status_code == 404
    monkeypatch.setattr(rp, 'SNAPSHOT', tmp_path)
    page = client.get(f'/strategies/research?id={H4}').get_data(as_text=True)
    assert '驗證狀態不可用' in page and 'DATA_NOT_AVAILABLE' in page and '查看封存回測報告' not in page


# --- Backtest page (design D3) --------------------------------------------------------------------------------------

@needs_snapshot
def test_full_period_equals_the_archive_and_years_are_labelled_slices(client):
    archived = json.loads(export.RESULT.read_text(encoding='utf-8'))['primary']
    full = rp.backtest(H4, 'FULL')['research']
    assert np.isclose(full['end_value'], 1e6 * (1 + archived['total_return_2x']), rtol=1e-9)
    assert np.isclose(full['benchmark_return']['value'], round(archived['benchmark_v2_total_return'] * 100, 2))
    assert full['integrity']['status'] == 'ARCHIVE_VERIFIED' and full['slice_note'] is None and full['labels'] == []
    for year in ('2023', '2024', '2025'):
        sliced = rp.backtest(H4, year)
        assert np.isclose(sliced['research']['end_value'] / 1e6 - 1, archived['yearly'][year]['strategy'], rtol=1e-9)
        assert sliced['research']['slice_note'] and sliced['metrics']['sharpe_ratio']['value'] is None
    scaled = rp.backtest(H4, 'FULL', 2e7)['research']
    assert scaled['labels'] == ['ILLUSTRATIVE_LINEAR', 'CAPACITY_WARNING'] and np.isclose(scaled['end_value'], 20 * full['end_value'])
    page = client.post('/backtest', data={'strategies': H4, 'research_period': '2024'}).get_data(as_text=True)
    assert 'research-banner' in page and rp.DISCLAIMER in page and '年度切片' in page and 'r3 DSR' in page


def test_research_backtest_never_runs_the_engine_or_persists(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, '_run_portfolio_backtest', lambda *a, **k: pytest.fail('engine called for a research strategy'))
    cases = [{'strategies': [H4, 'hybrid_trend_rank'], 'research_period': 'FULL'},
             {'strategies': H4, 'research_period': 'FULL', 'persist': '1'},
             {'strategies': H4, 'research_period': '2026'}, {'strategies': H4, 'research_period': '2016-2022'}]
    for data in cases:
        response = client.post('/backtest', data=data)
        assert response.status_code == 302 and '/backtest' in response.headers['Location']


# --- latest-data provider (design D6) -------------------------------------------------------------------------------

class _Provider:
    def __init__(self, day='2025-06-30', late=False):
        self.day, self.ctx, self.late = day, h4_signal.synthetic_context(day if day <= ss.DEV_END else '2025-06-30'), late

    def latest_session(self):
        return self.day

    def members(self, day):
        return self.ctx.data['members']

    def growth(self, day):
        available = '2025-07-15' if self.late else '2025-05-15'
        return {m: pd.DataFrame({'value': s, 'available_date': available}) for m, s in self.ctx.data['growth_day'].items()}

    def momentum(self, day):
        return self.ctx.data['momentum_day']

    def digests(self):
        return {'synthetic': 'seed'}


def test_latest_pipeline_with_a_synthetic_provider_inside_the_window():
    out = latest_signal.latest_signals(_Provider(), today='2025-07-01')
    assert out['status'] == 'OK' and out['manifest']['source_hash'] == ss.source_hash({'synthetic': 'seed'})
    assert list(out['signals'].columns[:len(ss.COMMON)]) == list(ss.COMMON) and (out['signals']['target_weight'] > 0).sum() == 10


@pytest.mark.parametrize('provider, today, code', [(_Provider(late=True), '2025-07-01', 'PIT_VIOLATION'),
                                                    (_Provider(), '2025-08-30', 'STALE_SESSION'), (_Provider(), '2025-06-01', 'FUTURE_SESSION')])
def test_latest_context_rejects_bad_provider_data(provider, today, code):
    with pytest.raises(latest_signal.LatestSignalError, match=code):
        latest_signal.build_latest_context(provider, today)


def test_latest_stays_disabled_and_the_date_guard_holds(client):
    assert latest_signal.readiness()['status'] == 'LATEST_SIGNAL_DISABLED'
    sealed = _Provider('2026-11-04')
    assert latest_signal.readiness(sealed)['reason'] == 'REQUIRES_SEALED_2026_DATA'
    with pytest.raises(ss.ResearchOnlyViolation, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        latest_signal.build_latest_context(sealed, today='2026-11-05')
    response = client.get(f'{API}/latest')
    body = response.get_json()
    assert response.status_code == 403 and body['status'] == 'LATEST_SIGNAL_DISABLED' and len(body['missing']) == 4
    assert not {'ticker', 'top', 'rows', 'signals'} & set(body)


# --- isolation (design D7) ------------------------------------------------------------------------------------------

def test_new_modules_have_no_production_path():
    forbidden = ('core.db_helper', 'jobs', 'linebot', 'core.line_message_builder', 'core.strategy_manager', 'execution', 'sqlalchemy')
    for path in ('app/research_payloads.py', 'core/research/latest_signal.py', 'scripts/export_h4_web_snapshot.py'):
        tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {m for m in names if any(m == f or m.startswith(f + '.') for f in forbidden)}, path
