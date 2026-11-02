from core.research.strategy_status import collect_evidence, derive, unavailable_metrics


def test_collect_evidence_uses_only_corrected_reruns_registry_and_ledger():
    registry = [{'hypothesis_id': 'H1', 'strategy': 'S'}, {'hypothesis_id': 'H2', 'strategy': 'OTHER'}]
    ledger = [{'kind': 'TRIAL', 'hypothesis_id': 'H1'},
              {'kind': 'OOS_FREEZE', 'hypothesis_id': 'H1', 'freeze_hash': 'f1'},
              {'kind': 'OOS_EVALUATION', 'freeze_hash': 'f1', 'outcome': 'ATTEMPT_STARTED'}]
    base = dict(registry_rows=registry, trial_rows=ledger, paper_states={'S': 'REHEARSAL'})
    evidence = collect_evidence('S', **base)
    assert evidence['freeze_hash'] == 'f1' and evidence['oos_outcome'] == 'ATTEMPT_WITHOUT_RESULT'
    assert evidence['gates'] == {} and evidence['bootstrap_lower'] is None and evidence['paper_passed'] is False
    assert collect_evidence('OTHER', **base)['freeze_hash'] is None
    old_fill = {'S': {'fill_rule': 'DIAGNOSTIC_ONLY', 'execution_audit_pass': True, 'gates': {'g1': 'PASS'}}}
    assert collect_evidence('S', **base, reruns=old_fill)['gates'] == {}
    corrected = {'S': {'fill_rule': 'CORRECTED_FILL_RULE', 'execution_audit_pass': True, 'gates': {'g1': 'PASS'}, 'bootstrap_lower': .2}}
    assert collect_evidence('S', **base, reruns=corrected)['gates'] == {'g1': 'PASS'}
    done = ledger + [{'kind': 'OOS_RESULT', 'freeze_hash': 'f1', 'outcome': 'FAIL'}]
    assert collect_evidence('S', **{**base, 'trial_rows': done})['oos_outcome'] == 'FAIL'
    twice = ledger + [{'kind': 'OOS_FREEZE', 'hypothesis_id': 'H1', 'freeze_hash': 'f2'}]
    assert collect_evidence('S', **{**base, 'trial_rows': twice})['oos_outcome'] == 'MULTIPLE_FREEZES'


def test_states_and_fail_closed_precedence():
    row = dict(DATA_STATUS='DATA_CERTIFIED', SIGNAL_STATUS='KEEP', BACKTEST_STATUS='CREDIBLE', STATISTICAL_STATUS='PASS')
    evidence = dict(gates={f'g{i}': 'PASS' for i in range(1, 6)}, bootstrap_lower=0.1)
    assert derive({**row, 'DATA_STATUS': 'MISSING_DATA'}, evidence)[0] == 'DATA_BLOCKED'
    assert 'OD-P6' in derive({**row, 'DATA_STATUS': 'DATA_BLOCKED', 'reasons': 'missing action terms OD-P6'}, evidence)[1]
    assert derive(row, {**evidence, 'bootstrap_lower': 0}) == ('VALIDATION_REJECTED', 'g2 bootstrap lower bound is not positive')
    assert derive(row, {**evidence, 'gates': {}})[0] == 'DATA_BLOCKED'
    assert derive(row, evidence)[0] == 'RESEARCH_CANDIDATE'
    frozen = {**evidence, 'freeze_hash': 'frozen'}
    assert derive(row, frozen)[0] == 'OOS_ELIGIBLE'
    assert derive(row, {**frozen, 'oos_outcome': 'FAIL'})[0] == 'VALIDATION_REJECTED'
    assert derive(row, {**frozen, 'oos_outcome': 'PENDING'})[0] == 'DATA_BLOCKED'
    assert derive(row, {**frozen, 'oos_outcome': 'PASS'})[0] == 'OOS_PASSED'
    assert derive(row, {**frozen, 'oos_outcome': 'PASS', 'paper_passed': True})[0] == 'PAPER_ELIGIBLE'
    assert all(metric == {'value': None, 'reason': 'not run'} for metric in unavailable_metrics('not run').values())
