import pytest
import numpy as np
from core.research.research_mode import screen_registration
from core.research.research_screen import screen
from scripts import research_workflow as workflow


def test_truncated_configuration_is_never_screened(tmp_path):
    registry = tmp_path / 'registry.csv'
    workflow.register(screen_registration(), path=registry)
    result = screen({'status': 'NOT_COMPUTABLE', 'reason': 'UNRESOLVED_CORPORATE_ACTION_HELD',
                     'net_returns_2x': [999]}, trial_sharpe_variance=1, registry=registry)
    assert result['status'] == 'NOT_COMPUTABLE' and result['candidate'] is False and 'gates' not in result
    registry.write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='REGISTRY_UNVERIFIED'):
        screen({'status': 'COMPUTABLE'}, trial_sharpe_variance=1, registry=registry)


def test_dsr_uses_larger_of_formal_baseline_and_admitted_trials(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.csv'
    workflow.register(screen_registration(), path=registry)
    values = .0001 + .01 * np.sin(np.arange(300))
    result = {'status': 'COMPUTABLE', 'net_returns_2x': values, 'excess_returns_2x': values,
              'top5_assets_profit_share': .3, 'best5pct_days_return_share': .3,
              'regime': {'bull': {'status': 'POSITIVE', 'sessions': 100}, 'bear': {'status': 'NEGATIVE', 'sessions': 100}}}
    monkeypatch.setattr(workflow, 'research_trials', lambda ledger: 5)
    assert screen(result, trial_sharpe_variance=.01, registry=registry)['dsr_trials'] == 743
    monkeypatch.setattr(workflow, 'research_trials', lambda ledger: 800)
    assert screen(result, trial_sharpe_variance=.01, registry=registry)['dsr_trials'] == 800


def test_v2_registration_keeps_v1_criteria_and_binds_benchmark_code(tmp_path):
    import hashlib
    from pathlib import Path
    import core.research.research_mode as mode
    v1, v2 = screen_registration(), screen_registration('V2')
    assert v2['hypothesis_id'] == 'RESEARCH-SCREEN:V2' and v2['falsification_test'] == v1['falsification_test']
    assert {k for k in v1 if v1[k] != v2[k]} == {'hypothesis_id', 'hypothesis', 'source'}
    module = Path(mode.__file__).with_name('benchmark_v2.py')
    assert hashlib.sha256(module.read_bytes()).hexdigest() in v2['hypothesis']
    registry = tmp_path / 'registry.csv'
    workflow.register(v1, path=registry)
    with pytest.raises(ValueError, match='REGISTRY_UNVERIFIED'):
        screen({'status': 'COMPUTABLE'}, trial_sharpe_variance=1, registry=registry, version='V2')
    workflow.register(v2, path=registry)
    assert screen({'status': 'NOT_COMPUTABLE'}, trial_sharpe_variance=1, registry=registry, version='V2')['status'] == 'NOT_COMPUTABLE'
    stale = tmp_path / 'stale.csv'
    workflow.register({**v2, 'hypothesis': v2['hypothesis'].replace(hashlib.sha256(module.read_bytes()).hexdigest(), '0' * 64)},
                      path=stale)
    with pytest.raises(ValueError, match='REGISTRY_UNVERIFIED'):
        screen({'status': 'COMPUTABLE'}, trial_sharpe_variance=1, registry=stale, version='V2')
