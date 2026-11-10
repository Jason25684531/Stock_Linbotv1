"""Corporate action evidence (design D2): development-window source only, partial coverage never certified. Temporary files only."""
import hashlib
import json

import pytest

from core.research import corporate_actions as ca


def _prepared(tmp_path, actions, unresolved=(), end='2025-12-31', tickers=('1101', '2330')):
    raw = json.dumps({'actions': list(actions), 'unresolved_events': list(unresolved), 'coverage': {t: ['2022-01-03', '2025-12-31'] for t in tickers}}).encode()
    (tmp_path / 'actions.json').write_bytes(raw)
    (tmp_path / 'input_manifest.json').write_text(json.dumps({'development_end': end, 'files': {'actions.json': hashlib.sha256(raw).hexdigest()}}))
    return tmp_path


CASH = {'ex_date': '2024-07-10', 'stock_id': '1101', 'type': 'cash', 'source_sha256': 'abc', 'cash_pay_date': '2025-12-31'}
STALE = {'date': '2024-07-10', 'stock_id': '1101', 'price_source_date': '2024-07-09', 'stale_days': 1}


def test_table_columns_and_no_inferred_dates(tmp_path):
    table = ca.load(_prepared(tmp_path, [CASH], [{'ex_date': '2024-03-01', 'stock_id': '2330', 'missing_terms': ['actual_effective_event_date']}]))
    assert table['status'] == 'PARTIALLY_VERIFIED' and [tuple(e) for e in table['events']] == [ca.COLUMNS] * 2
    cash, unresolved = table['events']
    assert cash['event_type'] == 'CASH_DIVIDEND' and cash['verification_status'] == 'EXCHANGE_REPORT_LOCAL_CACHE'
    assert cash['announcement_date'] is None and cash['effective_date'] is None and 'PLACEHOLDER' in cash['null_reason']
    assert unresolved['verification_status'] == 'UNRESOLVED_TERMS' and ca.action_dates(table)['2330'] == ['2024-03-01']


def test_source_reaching_into_2026_is_refused(tmp_path):
    with pytest.raises(ValueError, match='SEALED_WINDOW_IN_SOURCE'):
        ca.load(_prepared(tmp_path, [dict(CASH, ex_date='2026-01-05')]))
    with pytest.raises(ValueError, match='SOURCE_NOT_DEVELOPMENT_WINDOW'):
        ca.load(_prepared(tmp_path, [CASH], end='2026-10-23'))


def test_tampered_source_is_refused(tmp_path):
    folder = _prepared(tmp_path, [CASH])
    (folder / 'actions.json').write_text(json.dumps({'actions': [], 'unresolved_events': []}))
    with pytest.raises(ValueError, match='SOURCE_HASH_MISMATCH'):
        ca.load(folder)


def test_verdict_relabels_and_matches_events(tmp_path):
    table = ca.load(_prepared(tmp_path, [CASH]))
    blocked = ca.verdict([STALE], table)
    assert blocked['status'] == 'VALUATION_BLOCKED_CORPORATE_ACTION' and blocked['event_match'][0]['match'] == 'MATCHED_ACTION'
    quiet = ca.verdict([dict(STALE, date='2024-08-02', price_source_date='2024-08-01')], table)
    assert quiet['status'] == 'STALE_ACTIONS_PARTIALLY_VERIFIED' and quiet['coverage'] == 'PARTIALLY_VERIFIED'
    assert [m['match'] for m in quiet['event_match']] == ['NO_EVENT_IN_SOURCE']
    # fail-closed: an uncovered stock, or a covered stock outside its covered dates, keeps the actions unknown
    for outside in (dict(STALE, stock_id='6581'), dict(STALE, date='2026-01-05', price_source_date='2025-12-31')):
        unknown = ca.verdict([dict(STALE, date='2024-08-02', price_source_date='2024-08-01'), outside], table)
        assert unknown['status'] == 'VALUATION_BLOCKED_ACTIONS_UNKNOWN' and outside['stock_id'] in unknown['blocked']
        assert unknown['event_match'][1]['match'] == 'OUTSIDE_SOURCE_COVERAGE'
    assert ca.verdict([STALE])['status'] == 'VALUATION_BLOCKED_ACTIONS_UNKNOWN'


def test_real_prepared_source_stays_in_the_development_window():
    if not (ca.PREPARED / 'actions.json').is_file():
        pytest.skip('prepared_v6 not present')
    table = ca.load()
    assert max(e['ex_date'] for e in table['events']) <= '2025-12-31' and table['status'] == 'PARTIALLY_VERIFIED'
