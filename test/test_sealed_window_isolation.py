"""Design D13: every research entry point of this change refuses 2026 before reading data."""
import json
from pathlib import Path

import pandas as pd
import pytest

from core.research import strategy_signal as ss

ROOT = Path(__file__).resolve().parents[1]
SEALED = '2026-01-02'


def test_legacy_runner_refuses_before_reading(monkeypatch):
    from scripts import legacy_baseline_run as legacy
    calls = []
    with pytest.raises(ss.ResearchOnlyViolation):
        legacy.run_legacy('mean_reversion', end=SEALED, snapshot_builder=lambda *a: calls.append(a))
    assert not calls


def test_lab_entry_points_refuse(monkeypatch):
    from core.research import combination_lab as lab, condition_pool as cp
    opened = []
    monkeypatch.setattr(cp, 'inputs', lambda: opened.append(1))
    with pytest.raises(ss.ResearchOnlyViolation):
        cp.day_scores('c1', SEALED)
    with pytest.raises(ss.ResearchOnlyViolation):
        lab.admit('LAB-SINGLE-V1', {'round': lab.ROUND}, [], {}, data_end=SEALED)
    assert not opened


def test_composite_refuses():
    from core.strategy import composite
    from core.strategy.contracts import StrategyContext
    executor = composite.CompositeExecutor.__new__(composite.CompositeExecutor)
    with pytest.raises(ss.ResearchOnlyViolation):
        composite.CompositeExecutor.execute(executor, StrategyContext(asof_date=SEALED))


def test_run_store_refuses_research_nav_in_2026(tmp_path):
    from core.backtest import run_store
    record = {'strategy_id': 's', 'parameter_hash': 'p', 'source_hash': 's', 'data_version': 'd', 'comparison_group': 'LAB_ENGINE:x',
              'certification_status': 'NOT_CERTIFIED', 'status_class': 'EXPERIMENTAL'}
    empty = pd.DataFrame(columns=['stock_id'])
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        run_store.save_run(record, {'nav': pd.DataFrame({'date': [SEALED], 'nav': [1.0]}), 'holdings': empty, 'trades': empty,
                                    'picks': empty, 'metrics': {}}, root=tmp_path)


def test_benchmark_refuses_sealed_calendar():
    from core.research import benchmark_tri
    with pytest.raises(ss.ResearchOnlyViolation):
        benchmark_tri.quality(['2025-12-31', SEALED])


def test_factor_analysis_drops_windows_after_dev_end():
    from core.research import trade_analytics as ta
    panel = pd.DataFrame([{'asof_date': '2025-12-01', 'asset_id': str(i), 'factor_id': 'c1', 'value': float(i)} for i in range(40)])
    forward = pd.DataFrame([{'asof_date': '2025-12-01', 'asset_id': str(i), 'horizon': 20, 'window_end': '2026-01-05', 'forward_return': 0.01 * i}
                            for i in range(40)])
    result = ta.factor_analysis(panel, forward)
    assert result.get('status') == 'NOT_COMPUTABLE' or not result.get('rank_ic', {}).get('value')


@pytest.mark.skipif(not (ROOT / 'outputs/research_mode/prepared_v6/input_manifest.json').is_file(), reason='local research inputs')
def test_research_inputs_end_in_2025():
    manifest = json.loads((ROOT / 'outputs/research_mode/prepared_v6/input_manifest.json').read_text(encoding='utf-8'))
    assert manifest['development_end'] == '2025-12-31'
    universe = json.loads((ROOT / 'outputs/research_mode/h4_signals_v1/universe_20.json').read_text(encoding='utf-8'))
    assert max(r['asof_date'] for r in universe) <= '2025-12-31' and max(str(r['execution_date']) for r in universe) <= '2025-12-31'


def test_latest_api_stays_disabled_even_with_2026_market_rows(monkeypatch):
    import app as app_module
    from core.research import latest_signal
    monkeypatch.setattr(latest_signal, 'readiness', lambda provider=None: {'status': 'LATEST_SIGNAL_DISABLED', 'reason': 'REQUIRES_SEALED_2026_DATA',
                                                                          'latest_session': '2026-11-05', 'missing': ['OWNER_GRANT']})
    body = app_module.app.test_client().get('/api/selection/latest?strategy=mean_reversion').get_json()
    assert body['status'] == 'LATEST_SIGNAL_DISABLED' and 'ticker' not in body
