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
