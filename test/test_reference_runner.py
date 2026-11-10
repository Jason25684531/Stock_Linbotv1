"""21-strategy reference orchestrator (design D6, D7): catalog, six states, run identity, REUSE without the engine, isolation. No real engine."""
import pandas as pd
import pytest

from core.research.reference import catalog, runner


def test_catalog_has_the_21_web_strategies_once():
    from app import research_payloads
    from core.runtime import fundamental_production
    from core.strategy_manager import StrategyManager
    ids = [cap.strategy_id for cap in catalog.capabilities()]
    web = list(StrategyManager.STRATEGY_METADATA) + [fundamental_production.STRATEGY_ID] + list(research_payloads.research_ids())
    assert len(ids) == len(set(ids)) == 21 and set(ids) == set(web)
    assert 'H4:GROWTH-MOMENTUM-V2' in ids


@pytest.mark.parametrize('sid, state, reason', [
    ('H4:GROWTH-MOMENTUM-V2', 'GOVERNED_ONLY', 'H4_SEALED_RESEARCH'),
    ('growth_momentum_breakout', 'DATA_BLOCKED', 'MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME'),
    ('macd_cross', 'RUN', 'NO_IDENTICAL_RUN'),
])
def test_classification_precedence(sid, state, reason):
    cap = {c.strategy_id: c for c in catalog.capabilities()}[sid]
    assert runner.classify(cap, {}) == runner.Verdict(state, reason)


def test_reuse_and_missing_inputs_follow_the_fixed_order():
    cap = {c.strategy_id: c for c in catalog.capabilities()}['mean_reversion']
    assert runner.classify(cap, {'reuse_run': 'R1'}).state == 'REUSE'
    assert runner.classify(cap, {'missing_inputs': ['bars'], 'reuse_run': 'R1'}) == runner.Verdict('DATA_BLOCKED', 'INPUT_MISSING:bars')
    blocked = {c.strategy_id: c for c in catalog.capabilities()}['quality_growth']
    assert runner.classify(blocked, {'reuse_run': 'R1'}).state == 'DATA_BLOCKED'  # a static block wins over an old result


def test_identity_changes_with_every_input_class(tmp_path):
    code = tmp_path / 'a.py'
    code.write_text('x = 1\n', encoding='utf-8')
    base = dict(strategy_id='mean_reversion', config={'fee_rate': 0.001}, code_files=[code], inputs={'bars': 'h1', 'calendar': 'c1'})
    first = runner.run_identity(**base)
    assert first.startswith('RID1:') and runner.run_identity(**base) == first
    assert runner.run_identity(**{**base, 'config': {'fee_rate': 0.002}}) != first
    assert runner.run_identity(**{**base, 'inputs': {'bars': 'h2', 'calendar': 'c1'}}) != first
    assert runner.run_identity(**{**base, 'inputs': {'bars': 'h1', 'calendar': 'c2'}}) != first
    code.write_text('x = 2\n', encoding='utf-8')
    assert runner.run_identity(**base) != first


def _fake_execute(calls):
    def execute(cap, identity):
        calls.append(cap.strategy_id)
        nav = pd.DataFrame({'date': ['2025-12-30', '2025-12-31'], 'nav_base': [1_000_000.0, 1_010_000.0], 'nav_stress': [1_000_000.0, 1_005_000.0]})
        return {'nav': nav, 'trades': pd.DataFrame({'stock_id': ['2330']}), 'engine_error': None}
    return execute


def test_batch_counts_21_and_second_batch_reuses_without_the_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OUT', tmp_path / 'reference_mode')
    monkeypatch.setattr(runner, 'identities', lambda caps: {c.strategy_id: f'RID1:{c.strategy_id}' for c in caps})
    calls = []
    first = runner.run_batch(execute=_fake_execute(calls), workers=1)
    assert sum(first['counts'].values()) == 21 and first['counts']['RUN'] == 17 and first['counts']['GOVERNED_ONLY'] == 2
    assert len(calls) == 17
    second = runner.run_batch(execute=_fake_execute(calls), workers=1)
    assert second['counts']['REUSE'] == 17 and len(calls) == 17  # the engine was not called again
    row = {r['strategy_id']: r for r in second['rows']}['mean_reversion']
    assert row['metrics']['base']['total_return']['value'] == pytest.approx(0.01)
    assert row['levels'] == {'ENGINEERING_PASS': True, 'REPRODUCIBLE': True, 'DATA_TRUST_VERIFIED': False, 'CERTIFIED': False}
    assert not any('rank' in key or 'score' in key or 'best' in key for r in second['rows'] for key in r)  # no ranking fields (D3)


def test_reference_runs_refuse_2026_before_reading_anything():
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        runner.check_reference_window('2023-01-03', '2026-01-05')


def test_an_engine_exception_is_failed_and_the_batch_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OUT', tmp_path / 'reference_mode')
    monkeypatch.setattr(runner, 'identities', lambda caps: {c.strategy_id: f'RID1:{c.strategy_id}' for c in caps})
    calls = []
    good = _fake_execute(calls)

    def execute(cap, identity):
        if cap.strategy_id == 'macd_cross':
            raise RuntimeError('boom')
        return good(cap, identity)
    report = runner.run_batch(execute=execute, workers=1)
    row = {r['strategy_id']: r for r in report['rows']}['macd_cross']
    assert row['state'] == 'FAILED' and 'boom' in row['error'] and row['levels']['ENGINEERING_PASS'] is False
    assert report['counts']['FAILED'] == 1 and report['counts']['RUN'] == 16
