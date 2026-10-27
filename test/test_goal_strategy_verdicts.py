import json

import pytest

from core.research import shadow_selection as ss
from scripts.goal_strategy_verdicts import verdict


# ---- design section 2: one case per rule ----
def test_blocked_when_data_or_model_is_not_certified():
    assert verdict(data='DATA_BLOCKED', model='NOT_APPLICABLE', any_config_passes=True, data_gaps=False, frozen=True)[0] == 'BLOCKED'
    assert verdict(data='DATA_PASS', model='MODEL_BLOCKED', any_config_passes=True, data_gaps=False, frozen=True)[0] == 'BLOCKED'


def test_need_more_evidence_when_data_has_known_gaps_and_no_configuration_passes():
    assert verdict(data='DATA_PASS', model='NOT_APPLICABLE', any_config_passes=False, data_gaps=True, frozen=True)[0] == 'NEED_MORE_EVIDENCE'


def test_rejected_only_on_complete_data():
    assert verdict(data='DATA_PASS', model='NOT_APPLICABLE', any_config_passes=False, data_gaps=False, frozen=True)[0] == 'REJECTED'


def test_qualified_needs_a_passing_and_frozen_configuration():
    assert verdict(data='DATA_PASS', model='NOT_APPLICABLE', any_config_passes=True, data_gaps=False, frozen=True)[0] == 'QUALIFIED_FOR_FORWARD'
    assert verdict(data='DATA_PASS', model='NOT_APPLICABLE', any_config_passes=True, data_gaps=False, frozen=False)[0] == 'NEED_MORE_EVIDENCE'
    assert verdict(data='DATA_PASS', model='NOT_APPLICABLE', any_config_passes=True, data_gaps=True, frozen=True)[0] == 'NEED_MORE_EVIDENCE'


# ---- design section 3: the shadow gate ----
VERDICTS = {'A': ('NEED_MORE_EVIDENCE', 'no configuration passes OD-24'), 'B': ('BLOCKED', 'DATA_BLOCKED')}
PICK = {'stock_id': '2330', 'stock_name': 'TSMC', 'signal_date': '2026-10-22', 'strategy_version': 'v1', 'reason': 'r', 'key_factors': {'f': 1.0},
        'score': 0.9, 'rank': 1, 'data_asof': '2026-10-22', 'data_status': 'DATA_PASS', 'liquidity': 1e9, 'limit_status': 'NONE', 'risks': 'x', 'invalidation': 'y'}


def test_no_qualified_strategy_records_no_selection_and_rejects_unqualified_picks(tmp_path):
    record = ss.record_day('2026-10-22', VERDICTS, {'A': [PICK]}, tmp_path)
    assert record['status'] == 'NO_QUALIFIED_SELECTION' and record['picks'] == []
    assert record['rejected'] == {'A': 'REJECTED_UNQUALIFIED: NEED_MORE_EVIDENCE (no configuration passes OD-24)', 'B': 'REJECTED_UNQUALIFIED: BLOCKED (DATA_BLOCKED)'}
    assert record['broker'] == 'DISABLED' and record['advice'] == 'NOT_INVESTMENT_ADVICE'


def test_qualified_picks_need_every_field(tmp_path):
    verdicts = {**VERDICTS, 'Q': ('QUALIFIED_FOR_FORWARD', 'passes g1..g5, frozen')}
    with pytest.raises(ValueError, match='invalidation'):
        ss.record_day('2026-10-22', verdicts, {'Q': [{k: v for k, v in PICK.items() if k != 'invalidation'}]}, tmp_path)
    record = ss.record_day('2026-10-22', verdicts, {'Q': [PICK], 'A': [PICK]}, tmp_path)
    assert record['status'] == 'SELECTION' and [p['strategy'] for p in record['picks']] == ['Q'] and 'A' in record['rejected']


def test_a_day_is_recorded_once_and_any_edit_breaks_the_chain(tmp_path):
    ss.record_day('2026-10-21', VERDICTS, {}, tmp_path)
    ss.record_day('2026-10-22', VERDICTS, {}, tmp_path)
    with pytest.raises(ValueError, match='already recorded'):
        ss.record_day('2026-10-22', VERDICTS, {}, tmp_path)
    assert ss.verify(tmp_path) == {'status': 'PASS', 'records': 2}
    lines = (tmp_path / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()
    edited = json.loads(lines[0]) | {'status': 'SELECTION'}
    (tmp_path / 'ledger.jsonl').write_text('\n'.join([json.dumps(edited, ensure_ascii=False), lines[1]]) + '\n', encoding='utf-8')
    assert ss.verify(tmp_path) == {'status': 'FAIL', 'records': 2, 'first_bad_line': 1}


def test_truncating_the_tail_or_writing_after_a_broken_chain_is_detected(tmp_path):
    for day in ('2026-10-20', '2026-10-21', '2026-10-22'):
        ss.record_day(day, VERDICTS, {}, tmp_path)
    ledger = tmp_path / 'ledger.jsonl'
    lines = ledger.read_text(encoding='utf-8').splitlines()
    ledger.write_text('\n'.join(lines[:2]) + '\n', encoding='utf-8')  # last record removed
    assert ss.verify(tmp_path)['status'] == 'FAIL'
    with pytest.raises(ValueError, match='chain'):
        ss.record_day('2026-10-23', VERDICTS, {}, tmp_path)


def test_dates_must_be_iso_sessions(tmp_path):
    with pytest.raises(ValueError):
        ss.record_day('2026-10-22 ', VERDICTS, {}, tmp_path)
    assert not (tmp_path / ss.LEDGER).exists() and not (tmp_path / ss.HEAD).exists()  # a rejected date writes no ledger


def test_a_data_pass_strategy_without_any_backtest_is_not_rejected():
    assert verdict(data='DATA_PASS', model='NOT_APPLICABLE', any_config_passes=False, data_gaps=True, frozen=False)[0] == 'NEED_MORE_EVIDENCE'


def test_verdicts_r2_data_traces_to_the_newer_evidence():
    import pandas as pd
    from scripts.goal_strategy_verdicts import DATA_R2, OUT
    r2 = pd.read_csv(DATA_R2, encoding='utf-8-sig').set_index('family_name')['DATA']
    v = pd.read_csv(OUT / 'strategy_verdicts_r2.csv', encoding='utf-8-sig').set_index('strategy')
    assert all(v.loc[s, 'DATA'] == d for s, d in r2.items())
    assert (v.VERDICT != 'QUALIFIED_FOR_FORWARD').all()
