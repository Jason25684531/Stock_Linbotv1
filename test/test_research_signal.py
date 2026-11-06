"""h4-final-resolution-v1 ROUTE C: research signal format, H4 executor parity, replaceable strategies, research-only safety."""
import ast
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from core.research import h4_signal, strategy_signal as ss
from core.strategy.contracts import SelectionResult, StrategyContext
from core.strategy.lifecycle import StrategyLifecycle
from core.strategy.registry import StrategyRegistry
from core.strategy.spec import StrategySpec
from scripts import research_signal_run as rsr

ROOT = Path(__file__).resolve().parents[1]
H4_COLUMNS = list(ss.COMMON) + ['g2_score', 'g3_score', 'momentum_score']


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    research = tmp_path / 'outputs/research_mode'
    monkeypatch.setattr(ss, 'RESEARCH_DIR', research)
    return research / 'signal_shadow_v1'


def test_synthetic_run_writes_the_six_research_files(sandbox):
    first = rsr.run(h4_signal.STRATEGY_ID, h4_signal.synthetic_context('2025-03-31'), sandbox, mode='SYNTHETIC')
    second = rsr.run(h4_signal.STRATEGY_ID, h4_signal.synthetic_context('2025-06-30'), sandbox, mode='SYNTHETIC')
    assert sorted(p.name for p in second.iterdir()) == sorted(ss.FILES)
    signals = pd.read_csv(second / 'signals.csv', dtype={'ticker': str})
    assert list(signals.columns) == H4_COLUMNS
    ranked = signals.dropna(subset=['rank'])
    assert list(ranked['rank']) == list(range(1, len(ranked) + 1))
    assert (ranked['final_score'].diff().dropna() <= 0).all()
    assert np.isclose(signals['target_weight'].sum(), 1.0, rtol=1e-9, atol=1e-12) and signals['target_weight'].max() == .1
    unranked = signals.loc[signals['rank'].isna()]
    assert set(unranked['eligibility_status']) <= {'NO_GROWTH', 'NO_MOMENTUM', 'NOT_MEMBER'} and (unranked['target_weight'] == 0).all()
    rebalance = pd.read_csv(second / 'rebalance.csv', dtype={'ticker': str})
    previous = set(pd.read_csv(first / 'target_portfolio.csv', dtype={'ticker': str})['ticker'])
    assert set(rebalance.loc[rebalance['action'].eq('SELL'), 'ticker']) <= previous and rebalance['simulated_only'].all()
    assert np.isclose(rebalance['weight_change'].sum(), 0, atol=1e-12)
    perf = pd.read_csv(second / 'performance_record.csv')
    assert perf['status'].eq('NOT_OBSERVABLE').all() and perf['net_return_2x'].isna().all()
    manifest = json.loads((second / 'run_manifest.json').read_text(encoding='utf-8'))
    assert manifest['research_only'] is True and manifest['publish'] == 'DISABLED' and manifest['forward_observation'] is False
    assert manifest['lifecycle'] == 'RESEARCH'
    assert json.loads((second / 'data_health.json').read_text(encoding='utf-8'))['status'] == 'OK'


def test_first_run_buys_every_selected_ticker(sandbox):
    out = rsr.run(h4_signal.STRATEGY_ID, h4_signal.synthetic_context('2025-03-31'), sandbox, mode='SYNTHETIC')
    rebalance = pd.read_csv(out / 'rebalance.csv')
    assert rebalance['action'].eq('BUY').all() and len(rebalance) == 10


def test_data_health_flags_anomalies():
    signals = pd.DataFrame({'eligibility_status': ['NO_GROWTH'] * 5 + ['NOT_MEMBER']})
    health = ss.data_health(signals, {'bars': '2025-06-30', 'fundamentals': None}, '2025-06-27', {'members': 50, 'score_coverage': .9})
    assert {'NO_SCORED_MEMBER', 'INPUT_AFTER_SIGNAL_DATE:bars', 'INPUT_MISSING:fundamentals', 'MEMBER_COUNT_DROP_OVER_20PCT',
            'SCORE_COVERAGE_DROP_OVER_10PT'} <= set(health['anomalies']) and health['status'] == 'ANOMALY'


class _ValueExecutor:
    """Test-only second strategy: one component, no H4 code."""

    def execute(self, context, spec):
        values = context.data['value']
        frame = pd.DataFrame({'ticker': values.index, 'final_score': values.to_numpy(), 'eligibility_status': 'ELIGIBLE', 'value_score': values.to_numpy()})
        signals = ss.standardize(frame, signal_date=context.asof_date, top_n=spec.parameters['top_n'], strategy_version=spec.strategy_id,
                                 data_version='test', digest=ss.source_hash({'t': '1'}), components=('value_score',))
        return SelectionResult(metadata={'signals': signals, 'input_dates': {'value': context.asof_date}})


def test_a_second_strategy_runs_through_the_unchanged_runner(sandbox):
    spec = StrategySpec(strategy_id='TEST:VALUE-V1', display_name='test value', version='V1', lifecycle=StrategyLifecycle.RESEARCH,
                        parameters={'top_n': 3, 'rebalance_days': 20}, portfolio_policy={'max_weight': .34})
    registry = StrategyRegistry()
    registry.register(spec, factory=_ValueExecutor)
    context = StrategyContext(asof_date='2025-05-30', data={'value': pd.Series([.3, .9, .1, .5, .7], index=['1', '2', '3', '4', '5'])})
    out = rsr.run('TEST:VALUE-V1', context, sandbox, registry=registry)
    signals = pd.read_csv(out / 'signals.csv', dtype={'ticker': str})
    assert list(signals.columns) == list(ss.COMMON) + ['value_score']
    assert list(pd.read_csv(out / 'target_portfolio.csv', dtype={'ticker': str})['ticker']) == ['2', '5', '4']
    assert sorted(p.name for p in out.iterdir()) == sorted(ss.FILES)


def test_sealed_dates_and_outside_outputs_are_refused(sandbox, tmp_path):
    with pytest.raises(ss.ResearchOnlyViolation, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        rsr.run(h4_signal.STRATEGY_ID, h4_signal.synthetic_context('2026-01-05'), sandbox)
    late_input = h4_signal.synthetic_context('2025-06-30')
    late_input.data['input_dates']['bars'] = '2026-01-02'
    with pytest.raises(ss.ResearchOnlyViolation, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        rsr.run(h4_signal.STRATEGY_ID, late_input, sandbox)
    with pytest.raises(ss.ResearchOnlyViolation, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        h4_signal.dev_context('2026-01-05')
    with pytest.raises(ss.ResearchOnlyViolation, match='OUTPUT_OUTSIDE_RESEARCH_DIRECTORY'):
        rsr.run(h4_signal.STRATEGY_ID, h4_signal.synthetic_context('2025-06-30'), tmp_path / 'elsewhere')
    assert not (tmp_path / 'elsewhere').exists() and not sandbox.exists()


def test_research_modules_have_no_production_path():
    forbidden = ('core.db_helper', 'jobs.push_to_line', 'jobs.run_daily', 'jobs.scheduler', 'app', 'linebot', 'core.line_message_builder', 'core.strategy_manager')
    for path in ('core/research/strategy_signal.py', 'core/research/h4_signal.py', 'scripts/research_signal_run.py'):
        tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {m for m in names if any(m == f or m.startswith(f + '.') for f in forbidden)}, path
    code = ('import sys; from core.research import h4_signal; from scripts import research_signal_run; '
            'print(sorted(m for m in sys.modules if m in ("core.db_helper", "jobs.push_to_line", "linebot", "core.strategy_manager")))')
    loaded = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    assert loaded == '[]'


def test_h4_is_not_in_the_production_registry():
    from core.strategy.adapters.legacy import build_legacy_registry
    from core.strategy_manager import StrategyManager
    research = h4_signal.build_research_registry()
    assert research.canonical_ids() == (h4_signal.STRATEGY_ID,) and research.get_spec(h4_signal.STRATEGY_ID).lifecycle is StrategyLifecycle.RESEARCH
    assert h4_signal.STRATEGY_ID not in build_legacy_registry() and h4_signal.STRATEGY_ID not in StrategyManager.STRATEGY_METADATA


@pytest.fixture(scope='module')
def archived():
    root = ROOT / 'outputs/research_mode'
    if not (root / 'h4_signals_v1/scores_00_60.json').is_file():
        pytest.skip('archived H4 development signals are not present')
    scores = pd.DataFrame(json.loads((root / 'h4_signals_v1/scores_00_60.json').read_text(encoding='utf-8')))
    targets = pd.read_csv(root / 'h4_runs_v1/002/targets.csv', dtype={'asset_id': str})
    return scores, targets


@pytest.mark.parametrize('day', ['2025-09-26', '2025-12-26'])
def test_h4_executor_reproduces_the_archived_scores_and_targets(archived, day):
    scores, targets = archived
    result = h4_signal.H4Executor().execute(h4_signal.dev_context(day), h4_signal.SPEC)
    signals = result.metadata['signals']
    scored = signals.loc[signals['eligibility_status'].isin(['ELIGIBLE', 'EVENT_EXCLUDED'])].set_index('ticker')['final_score'].sort_index()
    expected = scores.loc[scores['asof_date'] == day].set_index('asset_id')['composite_score'].sort_index()
    assert list(scored.index) == list(expected.index)
    np.testing.assert_allclose(scored.to_numpy(), expected.to_numpy(), rtol=1e-9, atol=1e-12)
    chosen = signals.loc[signals['target_weight'] > 0]
    archived_day = targets.loc[targets['asof_date'].astype(str).str[:10] == day]
    assert sorted(chosen['ticker']) == sorted(archived_day['asset_id'])
    np.testing.assert_allclose(chosen.sort_values('ticker')['target_weight'].to_numpy(),
                               archived_day.sort_values('asset_id')['target_weight'].to_numpy(), rtol=1e-9, atol=1e-12)
    assert [r.stock_id for r in result.rows if r.selected] == list(chosen['ticker'])
