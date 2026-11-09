"""Regression tests for the 2026-11-06 independent review findings (status honesty, D3 rule, failed runs, uncovered scenarios)."""
import json

import pandas as pd

from core.backtest import run_store
from core.research import condition_pool as cp
from scripts import combination_lab_run as runner
from scripts import multi_strategy_report as report


def _ev(status, n, x, g4=False):
    return {'status': status, 'gates_comparison': {'values': {'net_sharpe_2x': n, 'excess_sharpe_2x': x}, 'gates': {'g4': g4}}}


NET = lambda e: ((e.get('gates_comparison') or {}).get('values') or {}).get('net_sharpe_2x')  # noqa: E731
EXC = lambda e: ((e.get('gates_comparison') or {}).get('values') or {}).get('excess_sharpe_2x')  # noqa: E731


def test_d3_needs_an_executed_candidate():
    base_negative = _ev('SUCCESS', -0.7, -0.9)
    assert runner.d3_recommendation(base_negative, {'status': 'NOT_TESTED'}, NET, EXC) == 'NOT_EVALUATED'
    assert runner.d3_recommendation(base_negative, _ev('SUCCESS', -0.2, -0.3), NET, EXC) == 'STOP_RESEARCH'
    assert runner.d3_recommendation(base_negative, _ev('SUCCESS', 0.4, 0.1, g4=True), NET, EXC) == 'REVISE'
    assert runner.d3_recommendation(_ev('SUCCESS', 0.5, 0.2), {}, NET, EXC) == 'KEEP'
    assert runner.d3_recommendation({'status': 'FAILED'}, {}, NET, EXC) == 'NOT_EVALUATED'


def test_outcome_per_hypothesis():
    assert runner.outcome_of(['SUCCESS', 'SUCCESS']) == 'EXPLORATORY_COMPLETE_UNCERTIFIED'
    assert runner.outcome_of(['FAILED']) == 'FAILED_ENGINE_ERROR'
    assert runner.outcome_of(['NOT_COMPUTABLE']) == 'NOT_COMPUTABLE_DATA_BLOCKED'
    assert runner.outcome_of(['SUCCESS', 'NOT_COMPUTABLE']).startswith('PARTIAL_')


def test_admitted_failure_reaches_the_run_store(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'ITEMS', tmp_path)
    monkeypatch.setattr(runner, 'run_legacy_item', lambda item, levels, registry: {'status': 'FAILED', 'reason': 'AttributeError: numerator',
                                                                                    'hypothesis_id': 'LEGACY-X-V1', 'experiment_id': 'e1'})
    item = {'position': 1, 'item_id': 'LEGACY:mean_reversion', 'kind': 'legacy_baseline', 'strategy_id': 'mean_reversion'}
    evidence = runner.run_item(item, None, 'TRI')
    run = run_store.load_run(evidence['failure_run_id'])['run']
    assert run['status'] == 'FAILED' and run['trial_id'] == 'e1' and run_store.latest('mean_reversion') is None
    saved = json.loads((tmp_path / 'LEGACY_mean_reversion.json').read_text(encoding='utf-8'))
    assert saved['failure_run_id'] == evidence['failure_run_id']


def test_report_status_rows_follow_the_evidence():
    rows = report.item_features([
        {'item_id': 'LEGACY:mean_reversion', 'item': {'kind': 'legacy_baseline'}, 'status': 'FAILED', 'reason': 'boom', 'failure_run_id': 'r1'},
        {'item_id': 'LEGACY:defensive_low_volatility', 'item': {'kind': 'legacy_baseline'}, 'status': 'SUCCESS', 'run_id': 'r2'},
        {'item_id': 'LEGACY-SENS:mean_reversion', 'item': {'kind': 'legacy_sensitivity'}, 'status': 'NOT_TESTED', 'reason': 'BASELINE_NOT_SUCCESS'}])
    assert [s for _, s, _ in rows] == ['FAILED', 'IMPLEMENTED_BUT_DATA_BLOCKED', 'NOT_TESTED']
    assert not any('已執行' in f or 'VERIFIED' in s for f, s, _ in report.FEATURES if 'Legacy' in f)


def test_benchmark_unavailable_is_reported(monkeypatch):
    from core.research import benchmark_tri

    def blocked(calendar, root=None):
        raise benchmark_tri.BenchmarkBlocked('IMPLEMENTED_BUT_AUTH_BLOCKED: no file')
    monkeypatch.setattr(benchmark_tri, 'research_calendar', lambda *a: ['2025-12-31'])
    monkeypatch.setattr(benchmark_tri, 'research_series', blocked)
    levels, note = runner.benchmark_levels()
    assert levels is None and note.startswith('TOTAL_RETURN_INDEX_UNAVAILABLE')
    assert runner.comparison_gates(None, {}, None) == {'status': 'NOT_COMPUTABLE', 'reason': 'BENCHMARK_UNAVAILABLE'}


def test_degenerate_condition_is_not_qualified(monkeypatch):
    days = ['2023-02-10', '2023-03-14']
    tickers = [str(i) for i in range(10)]
    monkeypatch.setattr(cp, 'inputs', lambda: {'days': days, 'members': {d: tickers for d in days}})
    monkeypatch.setattr(cp, 'score_panels', lambda: ({}, {c: ('DATA_BLOCKED', 'x') for c in cp.CONDITIONS if c not in ('c1', 'c2')}))
    same = pd.DataFrame({'score': [1.0] * 10, 'passes': [True] * 3 + [False] * 7}, index=tickers)
    varied = {d: pd.DataFrame({'score': list(range(10)), 'passes': [i % (n + 2) == 0 for i in range(10)]}, index=tickers)
              for n, d in enumerate(days)}
    monkeypatch.setattr(cp, 'day_scores', lambda cid, day: same if cid == 'c1' else varied[day])
    rows = {r['condition_id']: r for r in cp.qualify()[0]}
    assert rows['c1']['status'] == 'NOT_QUALIFIED' and 'DEGENERATE' in rows['c1']['reason']


def test_legacy_gate_is_capped_without_certified_data():
    summary = runner.legacy_summary([{'item_id': 'LEGACY:mean_reversion', **_ev('SUCCESS', 2.0, 1.5, g4=True), 'checks': {}}])
    assert summary['mean_reversion']['gate_result'] == 'DATA_BLOCKED'  # even a strong comparison value cannot become PASS
