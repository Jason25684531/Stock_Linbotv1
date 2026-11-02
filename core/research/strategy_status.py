"""Fail-closed research states derived from each strategy's own evidence."""
import math

METRICS = ('net_return', 'benchmark_excess_return', 'sharpe', 'deflated_sharpe',
           'bootstrap_confidence_interval', 'max_drawdown', 'volatility', 'turnover',
           'cost_stress', 'liquidity_stress', 'profit_concentration_stock',
           'profit_concentration_day', 'regime_performance', 'parameter_sensitivity',
           'cross_period_stability')


def derive(row, evidence):
    if row.get('DATA_STATUS') != 'DATA_CERTIFIED':
        reason = f"DATA_STATUS={row.get('DATA_STATUS', 'MISSING')}"
        if isinstance(row.get('reasons'), str) and row['reasons'].strip():
            reason += ': ' + row['reasons'].strip()
        return 'DATA_BLOCKED', reason
    gates = evidence.get('gates', {})
    for gate in ('g1', 'g2', 'g3', 'g4', 'g5'):
        if gates.get(gate) == 'FAIL':
            return 'VALIDATION_REJECTED', f'{gate}=FAIL'
    lower = evidence.get('bootstrap_lower')
    if isinstance(lower, (int, float)) and not isinstance(lower, bool) and math.isfinite(lower) and lower <= 0:
        return 'VALIDATION_REJECTED', 'g2 bootstrap lower bound is not positive'
    if (row.get('SIGNAL_STATUS') != 'KEEP' or row.get('BACKTEST_STATUS') != 'CREDIBLE'
            or row.get('STATISTICAL_STATUS') != 'PASS'
            or any(gates.get(gate) != 'PASS' for gate in ('g1', 'g2', 'g3', 'g4', 'g5'))
            or not isinstance(lower, (int, float)) or isinstance(lower, bool) or not math.isfinite(lower)):
        return 'DATA_BLOCKED', 'validation evidence incomplete or not credible'
    if not evidence.get('freeze_hash'):
        return 'RESEARCH_CANDIDATE', 'validation passed; no frozen identity'
    oos = evidence.get('oos_outcome')
    if oos is None:
        return 'OOS_ELIGIBLE', 'frozen; OOS not evaluated'
    if oos == 'FAIL':
        return 'VALIDATION_REJECTED', f'OOS={oos}'
    if oos != 'PASS':
        return 'DATA_BLOCKED', f'OOS attempt incomplete or unverifiable: {oos}'
    if evidence.get('paper_passed') is True:
        return 'PAPER_ELIGIBLE', 'OOS passed and paper ledger passed'
    return 'OOS_PASSED', 'OOS passed; paper ledger not passed'


def collect_evidence(strategy, *, registry_rows, trial_rows, paper_states, reruns=None):
    """Gather only admissible evidence for one strategy.

    Gates come from a corrected-fill rerun record (old-fill-rule statistics are PENDING, never a
    completed validation); freeze and OOS outcomes come from ledger rows of the strategy's registered
    hypotheses; paper state from the paper ledger. Anything absent stays None and derive() fails closed.
    """
    hypotheses = {row['hypothesis_id'] for row in registry_rows if row.get('strategy') == strategy}
    evidence = {'gates': {}, 'bootstrap_lower': None, 'freeze_hash': None, 'oos_outcome': None,
                'paper_passed': paper_states.get(strategy) == 'PASS'}
    rerun = (reruns or {}).get(strategy) or {}
    if rerun.get('fill_rule') == 'CORRECTED_FILL_RULE' and rerun.get('execution_audit_pass') is True:
        evidence['gates'] = dict(rerun.get('gates') or {})
        evidence['bootstrap_lower'] = rerun.get('bootstrap_lower')
    freezes = [row for row in trial_rows if row.get('kind') == 'OOS_FREEZE' and row.get('hypothesis_id') in hypotheses]
    if len(freezes) > 1:
        evidence.update(freeze_hash='CONFLICT', oos_outcome='MULTIPLE_FREEZES')
    elif freezes:
        freeze_hash = freezes[0]['freeze_hash']
        results = [row for row in trial_rows if row.get('kind') == 'OOS_RESULT' and row.get('freeze_hash') == freeze_hash]
        attempts = [row for row in trial_rows if row.get('kind') == 'OOS_EVALUATION' and row.get('freeze_hash') == freeze_hash]
        evidence['freeze_hash'] = freeze_hash
        evidence['oos_outcome'] = results[-1]['outcome'] if results else ('ATTEMPT_WITHOUT_RESULT' if attempts else None)
    return evidence


def unavailable_metrics(reason):
    return {name: {'value': None, 'reason': reason} for name in METRICS}
