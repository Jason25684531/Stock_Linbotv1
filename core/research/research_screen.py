"""Prerequisites for screening diagnostic configurations without promoting certification."""
import csv
import math
from pathlib import Path

import numpy as np

from core.research.research_mode import screen_registration
from scripts import research_workflow as workflow
from scripts import track_b_stats as stats


def screen(result, *, trial_sharpe_variance, registry=workflow.REGISTRY, research_ledger=workflow.RESEARCH_MODE_LEDGER):
    """Apply the registered OD-24 mathematics to a complete development result only."""
    if workflow.verify_registry(registry) != 'PASS':
        raise ValueError('REGISTRY_UNVERIFIED')
    with Path(registry).open(encoding='utf-8-sig', newline='') as stream:
        expected = screen_registration()
        matches = [row for row in csv.DictReader(stream) if row['hypothesis_id'] == expected['hypothesis_id']]
    if len(matches) != 1 or any(matches[0][k] != v for k, v in expected.items()):
        raise ValueError('REGISTRY_UNVERIFIED')
    if result.get('status') != 'COMPUTABLE':
        return {'status': 'NOT_COMPUTABLE', 'research_only': True, 'label': 'DIAGNOSTIC_ONLY',
                'reason': result.get('reason', 'INCOMPLETE_CONFIGURATION'), 'candidate': False}
    research_trials = workflow.research_trials(research_ledger)
    if not isinstance(research_trials, int) or isinstance(research_trials, bool) or research_trials < 1:
        raise ValueError('INVALID_RESEARCH_TRIAL_COUNT')
    if not math.isfinite(trial_sharpe_variance) or trial_sharpe_variance < 0:
        raise ValueError('UNDEFINED_TRIAL_SHARPE_VARIANCE')
    net = np.asarray(result['net_returns_2x'], dtype=float)
    excess = np.asarray(result['excess_returns_2x'], dtype=float)
    if len(net) != len(excess) or len(net) < 40 or not np.isfinite(net).all() or not np.isfinite(excess).all():
        return {'status': 'NOT_COMPUTABLE', 'research_only': True, 'label': 'DIAGNOSTIC_ONLY',
                'reason': 'INSUFFICIENT_OR_INVALID_RETURN_SERIES', 'candidate': False}
    n_trials = max(research_trials, 743)
    net_sharpe, excess_sharpe = stats.sharpe(net), stats.sharpe(excess)
    bootstrap = stats.block_bootstrap_sharpe(excess)
    dsr = stats.deflated_sharpe(excess, n_trials, trial_sharpe_variance)
    stock_share, day_share = result['top5_assets_profit_share'], result['best5pct_days_return_share']
    regimes = result['regime']
    statuses = [regimes[name]['status'] for name in ('bull', 'bear')]
    values = (net_sharpe, excess_sharpe, bootstrap['lower'], dsr, stock_share, day_share)
    if any(value is None or not math.isfinite(value) for value in values):
        return {'status': 'NOT_COMPUTABLE', 'research_only': True, 'label': 'DIAGNOSTIC_ONLY',
                'reason': 'UNDEFINED_SCREEN_METRIC', 'candidate': False}
    gates = {'r1': net_sharpe > 0 and excess_sharpe > 0, 'r2': bootstrap['lower'] > 0,
             'r3': dsr > .95, 'r4': stock_share < .5 and day_share < .5,
             'r5': all(regimes[name]['sessions'] >= 60 for name in ('bull', 'bear')) and statuses != ['NEGATIVE', 'NEGATIVE']
                   and all(s in ('NEGATIVE', 'POSITIVE') for s in statuses)}
    return {'status': 'RESEARCH_RUN_PASS_SCREEN' if all(gates.values()) else 'RESEARCH_RUN_FAIL_SCREEN',
            'label': 'DIAGNOSTIC_ONLY', 'research_only': True, 'candidate': all(gates.values()), 'gates': gates,
            'values': {'net_sharpe_2x': net_sharpe, 'excess_sharpe_2x': excess_sharpe,
                       'bootstrap_lower': bootstrap['lower'], 'deflated_sharpe': dsr,
                       'top5_assets_profit_share': stock_share, 'best5pct_days_return_share': day_share, 'regime': regimes},
            'research_trials': research_trials, 'certification_baseline_trials': 743, 'dsr_trials': n_trials}
