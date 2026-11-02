import pytest

from core.research import repair_runner
from core.research.repair_runner import REQUIRED_TERMS, held_event_terms

SOURCE = 'e' * 64


@pytest.fixture
def fixture_source(monkeypatch):
    """A test dataset allowed to certify every field for the exact (stock, event_date) it was requested for."""
    monkeypatch.setitem(repair_runner.MATCHERS, 'FIXTURE', lambda p, r: (p['stock'], p['date']) == (r['stock_id'], r['event_date']))
    monkeypatch.setattr(repair_runner, 'CERTIFYING', {f: ('FIXTURE',) for f in (*REQUIRED_TERMS, 'event_list_complete')})
    return {SOURCE: [{'dataset': 'FIXTURE', 'parameters': {'stock': 'A', 'date': d}} for d in ('', '2025-07-01')]}


def _row(field, value, stock='A', day='2025-07-01', tier='OFFICIAL_VERIFIED_FIELD', sha256=SOURCE):
    return {'stock_id': stock, 'event_date': day, 'field': field, 'value_or_UNKNOWN': value, 'tier': tier, 'sha256': sha256}


def _complete(**overrides):
    values = {'ex_date': '2025-07-01', 'cash_dividend': 1.5, 'stock_dividend': 'NOT_APPLICABLE', 'cash_pay_date': '2025-07-25',
              'stock_credit_date': 'NOT_APPLICABLE', 'rights_terms': 'NOT_APPLICABLE', 'publication_time': '2025-06-10T17:00:00+08:00'}
    values.update(overrides)
    return [_row('event_list_complete', True, day='')] + [_row(field, values[field]) for field in REQUIRED_TERMS]


def test_official_terms_with_sourced_not_applicable_pass(fixture_source):
    assert held_event_terms(_complete(), {'A'}, {('A', '2025-07-01')}, fixture_source)['status'] == 'VERIFIED'


def test_each_question_blocks_separately(fixture_source):
    def first(rows, events=(('A', '2025-07-01'),)):
        return held_event_terms(rows, {'A'}, set(events), fixture_source)
    assert first(_complete(cash_pay_date='UNKNOWN'))['events'][0]['blocked_fields'] == {'cash_pay_date': 'UNKNOWN'}
    for published, reason in (('2025-07-01T08:00:00+08:00', 'NOT_PUBLISHED_BEFORE_EX_DATE'),
                              ('2025-06-30T17:00:00+00:00', 'NOT_PUBLISHED_BEFORE_EX_DATE'),  # 01:00 on the ex-date in Taipei
                              ('2025-06-10T17:00:00', 'INVALID_PUBLICATION_TIME'),  # no offset
                              ('114/06/10', 'INVALID_PUBLICATION_TIME')):
        assert first(_complete(publication_time=published))['events'][0]['blocked_fields'] == {'publication_time': reason}
    rows = _complete()
    rows[3] = _row('stock_dividend', 'NOT_APPLICABLE', tier='LOCAL_CACHE_UNCERTIFIED')  # not applicable needs an official source
    assert first(rows)['events'][0]['blocked_fields'] == {'stock_dividend': 'TIER_LOCAL_CACHE_UNCERTIFIED'}
    no_list = first(_complete()[1:])
    assert no_list['status'] == 'BLOCKED' and no_list['stocks_event_list_unverified'] == ['A'] and no_list['blocked'] == 0
    assert set(first(_complete(), events=(('A', '2025-08-01'),))['events'][0]['blocked_fields']) == set(REQUIRED_TERMS)


def test_a_verified_hash_certifies_only_its_own_request(fixture_source):
    assert set(held_event_terms(_complete(), {'A'}, {('A', '2025-07-01')}, {})['events'][0]['blocked_fields'].values()) == {'UNBOUND_OFFICIAL_ROW'}
    other_stock = {SOURCE: [{'dataset': 'FIXTURE', 'parameters': {'stock': 'B', 'date': d}} for d in ('', '2025-07-01')]}
    assert set(held_event_terms(_complete(), {'A'}, {('A', '2025-07-01')}, other_stock)['events'][0]['blocked_fields'].values()) == {'UNBOUND_OFFICIAL_ROW'}


def test_real_configuration_binds_detail_requests_and_cannot_certify_event_lists():
    rows = _complete()
    detail = {SOURCE: [{'dataset': 'TWT49U_DETAIL', 'parameters': {'STK_NO': 'A', 'T': '20250701', 'response': 'json'}}],
              'f' * 64: [{'dataset': 'TWTAUU', 'parameters': {}}]}
    result = held_event_terms(rows, {'A'}, {('A', '2025-07-01')}, detail)
    # Detail certifies amounts and rights terms only; payment, credit and publication times need MOPS, and so do event lists.
    assert result['events'][0]['blocked_fields'] == {f: 'UNBOUND_OFFICIAL_ROW' for f in ('cash_pay_date', 'stock_credit_date', 'publication_time')}
    assert result['stocks_event_list_unverified'] == ['A'] and result['verified_sources'] == 2
    other_request = {SOURCE: [{'dataset': 'TWT49U_DETAIL', 'parameters': {'STK_NO': 'B', 'T': '20250701', 'response': 'json'}}]}
    assert held_event_terms(rows, {'A'}, {('A', '2025-07-01')}, other_request)['events'][0]['blocked_fields']['cash_dividend'] == 'UNBOUND_OFFICIAL_ROW'
    assert held_event_terms(rows, {'A'}, {('A', '2025-07-01')}, None)['verified_sources'] == 'AUDIT_UNREADABLE'
    wrong_date = _complete(ex_date='2025-07-02')
    assert held_event_terms(wrong_date, {'A'}, {('A', '2025-07-01')}, detail)['events'][0]['blocked_fields']['ex_date'] == 'EX_DATE_VALUE_MISMATCH'
    unrelated = [_row(f, v, sha256='f' * 64) for f, v in (('cash_dividend', 1.5),)]  # a verified t1b hash certifies no dividend
    assert held_event_terms(rows[:2] + unrelated, {'A'}, {('A', '2025-07-01')}, detail)['events'][0]['blocked_fields']['cash_dividend'] == 'UNBOUND_OFFICIAL_ROW'


def test_a_later_row_supersedes_and_unknown_is_never_zero(fixture_source):
    def status(rows):
        return held_event_terms(rows, {'A'}, {('A', '2025-07-01')}, fixture_source)['status']
    assert status(_complete(cash_dividend='UNKNOWN') + [_row('cash_dividend', 0)]) == 'VERIFIED'  # an official zero is a value
    assert status(_complete() + [_row('cash_dividend', 'UNKNOWN')]) == 'BLOCKED'


def test_verified_sources_reads_only_verified_attempts_and_fails_closed(tmp_path, monkeypatch):
    import json
    import sqlite3
    from contextlib import contextmanager
    from core.runtime import research_budget
    conn = sqlite3.connect(tmp_path / 'audit.sqlite3')
    conn.execute('CREATE TABLE attempts (sha256 TEXT, context TEXT, outcome TEXT)')
    context = json.dumps({'dataset': 'TWT49U_DETAIL', 'parameters': [['STK_NO', 'A'], ['T', '20250701'], ['response', 'json']]})
    conn.executemany('INSERT INTO attempts VALUES (?, ?, ?)', [(SOURCE, context, 'VERIFIED'), ('f' * 64, context, 'FAILED'),
                                                                (None, context, 'LEGACY')])
    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(research_budget, 'connection', connection)
    assert repair_runner.verified_sources() == {SOURCE: [{'dataset': 'TWT49U_DETAIL',
                                                          'parameters': {'STK_NO': 'A', 'T': '20250701', 'response': 'json'}}]}
    def broken():
        raise research_budget.BudgetDenied('BUDGET_STATE_INVALID: anchor mismatch')
    monkeypatch.setattr(research_budget, 'connection', broken)
    assert repair_runner.verified_sources() is None
