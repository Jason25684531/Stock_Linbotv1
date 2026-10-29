"""Seven-layer qualification matrix: fail-closed, no circular PAPER dependency, research shadow never Fresh OOS, old-rule stats labelled."""
import pandas as pd
import pytest

from scripts import goal_strategy_verdicts as gv
from scripts import track_b_evaluate as tb


def frames(data_r2='DATA_PASS', v2_data='DATA_PASS', v1_data='DATA_PASS', passing=1, configs=12, sessions=0, fwd_status='PENDING_FORWARD_EVIDENCE', family='G2G3'):
    status = pd.DataFrame([{'group': 'B', 'strategy': 'S'}])
    r2 = pd.DataFrame([{'family_name': 'S', 'DATA': data_r2, 'blockers': 'OD-P6'}]) if data_r2 else pd.DataFrame()
    v2 = pd.DataFrame([{'strategy': 'S', 'DATA': v2_data, 'configurations': configs, 'configurations_passing_all_od24': passing}])
    v1 = pd.DataFrame([{'strategy': 'S', 'DATA': v1_data, 'configurations': configs, 'configurations_passing_all_od24': passing}])
    diag = pd.DataFrame([{'strategy': 'S', 'family': family, 'factor_ic_60d': 'IC 0.03'}])
    fwd = pd.DataFrame([{'strategy': 'S', 'post_freeze_sessions_recorded': sessions, 'status': fwd_status}])
    return status, r2, v2, v1, diag, fwd


RERUN_OK = {'S': {'execution_audit_pass': True, 'configurations_passing_all_od24': 1, 'fill_rule': 'CORRECTED_FILL_RULE'}}
RERUN_FAIL = {'S': {'execution_audit_pass': True, 'configurations_passing_all_od24': 0, 'fill_rule': 'CORRECTED_FILL_RULE'}}
CERTIFIED = {'S': {k: ('PASS', f'evidence/{k}.md') for k in gv.CERTIFICATION_ITEMS}}


def run(shadow=None, paper=None, reruns=None, evidence=None, certifications=None, **kw):
    m, conflicts, _ = gv.build_matrix(*frames(**kw), shadow or {}, paper or {}, reruns, evidence, certifications)
    return m.iloc[0], conflicts


def test_data_blocked_never_qualifies_even_if_everything_else_passes():
    row, _ = run(data_r2='DATA_BLOCKED', sessions=300, fwd_status='FRESH_OOS_PASS', paper={'S': 'PASS'}, reruns=RERUN_OK)
    assert row.DATA_STATUS == 'DATA_BLOCKED' and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED' and 'OD-P6' in row.reasons


def test_conflict_takes_conservative_value_and_is_recorded():
    row, conflicts = run(data_r2='DATA_BLOCKED', v1_data='DATA_PASS')
    assert row.DATA_STATUS == 'DATA_BLOCKED'
    assert conflicts == ['S: DATA p0_strategy_data_status_r2.csv=DATA_BLOCKED vs strategy_verdicts_r2.csv=DATA_PASS vs strategy_verdicts.csv=DATA_PASS']


def test_missing_source_is_missing_data_not_pass():
    status, _, v2, v1, diag, fwd = frames()
    m, _, blockers = gv.build_matrix(status, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), diag, fwd, {}, {})
    assert blockers[0]['blocker_id'] == 'NO_CERTIFICATION' and blockers[0]['date_range'] == 'UNKNOWN'
    assert m.iloc[0].DATA_STATUS == 'MISSING_DATA' and m.iloc[0].FINAL_QUALIFICATION == 'NOT_QUALIFIED'


def test_research_shadow_prospective_is_never_fresh_oos():
    row, _ = run(shadow={'S': 40})
    assert row.FORWARD_STATUS == 'RESEARCH_SHADOW_ACCUMULATING(40)' and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED'
    row, _ = run(sessions=300, fwd_status='PENDING_FORWARD_EVIDENCE')
    assert row.FORWARD_STATUS == 'NONE'  # session count alone is not a pass


def test_old_fill_rule_statistics_keep_the_suffix_until_a_rerun_supplies_its_own():
    row, _ = run(passing=1)
    assert row.STATISTICAL_STATUS == 'PASS_OLD_FILL_RULE' and row.BACKTEST_STATUS == 'PENDING_CORRECTED_RERUN' and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED'
    row, _ = run(passing=1, reruns=RERUN_FAIL)  # the old PASS never survives a rerun that fails
    assert row.STATISTICAL_STATUS == 'FAIL' and row.BACKTEST_STATUS == 'CREDIBLE'
    row, _ = run(passing=0, reruns={'S': {'execution_audit_pass': False, 'configurations_passing_all_od24': 1}})
    assert row.BACKTEST_STATUS == 'DIAGNOSTIC_ONLY' and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED'  # audit failed: never credible


def test_vectorbt_family_is_at_most_diagnostic_only():
    row, _ = run(family='DAY3', reruns=RERUN_OK)
    assert row.BACKTEST_STATUS == 'DIAGNOSTIC_ONLY' and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED'


def test_paper_is_decided_after_the_five_stages_and_does_not_feed_back():
    row, _ = run(paper={'S': 'PASS'})  # signal NEED_MORE_EVIDENCE -> five stages fail
    assert row.PAPER_STATUS == 'PAPER_REHEARSAL' and row.SIGNAL_STATUS == 'NEED_MORE_EVIDENCE'
    gv.SIGNAL_OVERRIDES['S'] = 'KEEP'
    try:
        row, _ = run(reruns=RERUN_OK, sessions=300, fwd_status='FRESH_OOS_PASS', paper={'S': 'PASS'}, certifications=CERTIFIED)
        assert row.FINAL_QUALIFICATION == 'QUALIFIED' and row.PAPER_STATUS == 'PAPER_PASSED'  # only when every stage passes
        row, _ = run(reruns=RERUN_OK, sessions=300, fwd_status='FRESH_OOS_PASS', paper={}, certifications=CERTIFIED)
        assert row.PAPER_STATUS == 'NOT_ELIGIBLE' and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED'
    finally:
        del gv.SIGNAL_OVERRIDES['S']


def test_od24_gate_literals_and_forward_minimum_unchanged():
    src = (gv.ROOT / 'scripts/track_b_evaluate.py').read_text(encoding='utf-8')
    for literal in ("r.sharpe_net_2x_cost > 0 and r.excess_sharpe_2x_cost > 0", "ci['lower'] > 0", "dsr > 0.95",
                    "r.top5_assets_profit_share < 0.5 and r.best5pct_days_return_share < 0.5", "statuses == ['NEGATIVE', 'NEGATIVE']"):
        assert literal in src, literal  # g1..g5 as archived on 2026-10-23
    assert gv.FRESH_MINIMUM == 252 and (tb.CAPS, tb.UNFILLABLE_MATERIAL, tb.OVER_CAP_MATERIAL) == ((0.01, 0.02, 0.05), 0.01, 0.05)


def test_shadow_counter_ignores_backfilled_blocked_and_annotations(tmp_path):
    import json
    lines = [{'date': '1', 'strategy': 'A', 'sha256': 'x'}, {'kind': 'ANNOTATION', 'refers_to_sha256': 'x', 'status': 'BACKFILLED_INIT'},
             {'kind': 'BLOCKED', 'strategy': 'A', 'sha256': 'y'}, {'date': '2', 'strategy': 'A', 'sha256': 'z'}]
    p = tmp_path / 'ledger.jsonl'
    p.write_text('\n'.join(json.dumps(x) for x in lines), encoding='utf-8')
    assert gv.shadow_prospective_counts(p) == {'A': 1}


@pytest.mark.skipif(not (gv.DT / 'strategy_status_matrix.csv').is_file(), reason='archived evidence absent')
def test_real_matrix_has_22_rows_and_no_qualified_selection():
    import pandas as pd
    read = lambda p: pd.read_csv(p, encoding='utf-8-sig') if p.is_file() else pd.DataFrame()
    m, _, _ = gv.build_matrix(read(gv.DT / 'strategy_status_matrix.csv'), read(gv.DATA_R2), read(gv.OUT / 'strategy_verdicts_r2.csv'), read(gv.OUT / 'strategy_verdicts.csv'),
                           read(gv.P1_R2), read(gv.OUT / 'forward_evidence_status_r2.csv'), gv.shadow_prospective_counts(), gv.paper_account_states())
    assert len(m) == 22 and (m.FINAL_QUALIFICATION == 'NOT_QUALIFIED').all()
    assert m.set_index('strategy').SIGNAL_STATUS['D5_PORTFOLIO_5_FACTORS'] == 'PAUSE'
    assert m.set_index('strategy').SIGNAL_STATUS['A191_132'] == 'NEED_MORE_EVIDENCE'
    assert not m.FORWARD_STATUS.eq('FRESH_OOS_PASS').any() and not m.BACKTEST_STATUS.eq('CREDIBLE').any()


def test_all_sources_pass_without_a_certification_record_is_missing_data():
    """No detected blocker is not evidence: certification needs a positive record for every item."""
    row, _ = run()
    assert row.DATA_STATUS == 'MISSING_DATA' and 'no positive certification record' in row.source_DATA_STATUS and row.FINAL_QUALIFICATION == 'NOT_QUALIFIED'
    partial = {'S': {**CERTIFIED['S'], 'pit_fundamentals': ('PASS', '')}}  # a PASS without its evidence path does not count
    assert run(certifications=partial)[0].DATA_STATUS == 'MISSING_DATA'
    assert run(certifications=CERTIFIED)[0].DATA_STATUS == 'DATA_CERTIFIED'


def test_pit_conflict_and_blocker_rows():
    row, _ = run(data_r2='DATA_BLOCKED', evidence={'S': [('status:DATA_detail', 'prices/calendar certified; fundamentals values unverified')]})
    assert row.DATA_STATUS == 'PIT_UNVERIFIED'
    assert run(data_r2=None, v2_data='DATA_PASS', v1_data='UNKNOWN')[0].DATA_STATUS == 'SOURCE_CONFLICT'
    *_, rows = gv.build_matrix(*frames(data_r2='DATA_BLOCKED'), {}, {}, None,
                               {'S': [('p0', 'TOTAL_RETURN_BLOCKED: holds rights offerings; NONOFFICIAL_VALUATION_PRICE (OD-P5)'), ('n7', 'SPLIT_CAPITAL_REDUCTION_COMPLETENESS_UNKNOWN')]})
    assert [r['blocker_id'] for r in rows] == ['OD-P6', 'OD-P5', 'T1b'] and all(r['owner_required'].startswith('YES') for r in rows)
    assert set(rows[0]) == {'strategy', 'blocker_id', 'date_range', 'fields', 'evidence', 'release_condition', 'owner_required'}


def test_rerun_without_the_corrected_fill_rule_is_never_credible():
    for rule in ('DIAGNOSTIC_ONLY', 'OLD_FILL_RULE', None):
        row, _ = run(reruns={'S': {**RERUN_OK['S'], 'fill_rule': rule}})
        assert row.BACKTEST_STATUS == 'DIAGNOSTIC_ONLY'


def test_matrix_out_dir_writes_beside_the_2026_10_26_evidence(tmp_path):
    """matrix --out: a later round regenerates from the same archived inputs into its own folder; the earlier files are untouched."""
    before = {p.name: p.read_bytes() for p in gv.MATRIX_OUT.glob('*') if p.is_file()}
    gv.matrix(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} >= {'strategy_qualification_matrix.csv', 'data_blockers.csv', 'qualification_conflicts.md'}
    assert {p.name: p.read_bytes() for p in gv.MATRIX_OUT.glob('*') if p.is_file()} == before
    frame = pd.read_csv(tmp_path / 'strategy_qualification_matrix.csv', encoding='utf-8-sig')
    assert len(frame) == 22 and set(frame.FINAL_QUALIFICATION) == {'NOT_QUALIFIED'}
