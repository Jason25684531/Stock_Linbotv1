"""Publishing freshness against the trading calendar and LINE PENDING / SENT / UNKNOWN states (design D10). No real LINE call."""
from datetime import date

import pytest

from core.research import trading_calendar
from jobs import push_to_line


def _calendar(closed=(), state='EXPECTED_OPEN'):
    return lambda day: 'CLOSED_OBSERVED' if day.isoformat() in closed or day.weekday() >= 5 else state


def test_an_unverified_calendar_blocks_the_push_even_when_the_dates_agree():
    """Owner one-shot brief 2026-11-09 (R6): 三者不一致，或日曆未驗證時，禁止推送."""
    ok, expected, reason = trading_calendar.publish_freshness('2026-11-05', '2026-11-05', date(2026, 11, 5), 'evening',
                                                              status_fn=_calendar(state='CALENDAR_UNVERIFIED'))
    assert not ok and expected == '2026-11-05' and 'CALENDAR_UNVERIFIED' in reason


def test_both_dates_stalled_are_blocked_against_the_expected_session():
    ok, expected, reason = trading_calendar.publish_freshness('2026-10-22', '2026-10-22', date(2026, 11, 5), 'evening', status_fn=_calendar())
    assert not ok and expected == '2026-11-05' and 'EXPECTED_SESSION:2026-11-05' in reason
    assert trading_calendar.publish_freshness('2026-11-05', '2026-11-05', date(2026, 11, 5), 'evening', status_fn=_calendar())[0]
    assert not trading_calendar.publish_freshness('2026-11-05', '2026-11-04', date(2026, 11, 5), 'evening', status_fn=_calendar())[0]


def test_morning_expects_the_previous_open_session_and_skips_closures():
    ok, expected, _ = trading_calendar.publish_freshness('2026-10-22', '2026-10-22', date(2026, 10, 24), 'morning',
                                                         status_fn=_calendar(closed={'2026-10-23'}))
    assert ok and expected == '2026-10-22'  # 10-23 typhoon closure observed, 10-24 Saturday


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(push_to_line, 'PUBLISH_DIR', tmp_path / 'line_publish')
    return tmp_path / 'line_publish'


def test_sent_is_never_resent(ledger):
    sent = []
    assert push_to_line.publish_once('2026-11-05_evening', lambda: sent.append(1)) == 'SENT'
    assert push_to_line.publish_once('2026-11-05_evening', lambda: sent.append(1)) == 'ALREADY_SENT' and sent == [1]


def test_an_exception_during_send_is_unknown_and_blocks_blind_resends(ledger):
    def broken():
        raise RuntimeError('timeout after request')
    with pytest.raises(RuntimeError):
        push_to_line.publish_once('2026-11-05_evening', broken)
    assert push_to_line.publish_state('2026-11-05_evening') == 'UNKNOWN'
    with pytest.raises(push_to_line.PublishStateUnresolved):
        push_to_line.publish_once('2026-11-05_evening', lambda: pytest.fail('no blind resend'))


def test_pending_is_never_taken_as_sent(ledger):
    ledger.mkdir()
    (ledger / '2026-11-05_morning.json').write_text('{"state": "PENDING"}', encoding='utf-8')
    with pytest.raises(push_to_line.PublishStateUnresolved):
        push_to_line.publish_once('2026-11-05_morning', lambda: pytest.fail('no send'))
    assert push_to_line.publish_state('2026-11-05_morning') == 'PENDING'


def test_main_blocks_on_calendar_staleness_and_on_unresolved_state(ledger, monkeypatch):
    calls = []
    monkeypatch.setattr(push_to_line, '_recommendation_freshness_issue', lambda: (False, '2026-10-22', '2026-10-22'))
    monkeypatch.setattr(push_to_line, '_calendar_freshness', lambda market, rec, slot: (False, '2026-11-05', 'EXPECTED_SESSION:2026-11-05'))
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_start', lambda **kw: None)
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_finish', lambda **kw: calls.append(kw))
    monkeypatch.setattr(push_to_line, 'run_evening', lambda **kw: pytest.fail('stale push must not start'))
    assert push_to_line.main(['--time', 'evening']) == 2 and calls[-1]['status'] == 'blocked'
    monkeypatch.setattr(push_to_line, '_calendar_freshness', lambda market, rec, slot: (True, '2026-10-22', ''))
    ledger.mkdir(exist_ok=True)
    (ledger / '2026-10-22_evening.json').write_text('{"state": "UNKNOWN"}', encoding='utf-8')
    assert push_to_line.main(['--time', 'evening']) == 2 and 'PUBLISH_STATE_UNRESOLVED' in calls[-1]['error_summary']


def test_a_cached_error_reply_is_never_a_closure(tmp_path):
    """Interrogation finding (Codex): only TWSE's explicit no-data reply is a closure (decision.md D5)."""
    import json
    (tmp_path / 'MI_INDEX_20261109.json').write_text(json.dumps({'stat': 'ERROR'}), encoding='utf-8')
    fn = lambda d: trading_calendar.status(d, folders=[tmp_path], closures=tmp_path / 'missing.json')
    assert fn(date(2026, 11, 9)) == 'CALENDAR_UNVERIFIED'
    assert not trading_calendar.publish_freshness('2026-11-06', '2026-11-06', date(2026, 11, 9), 'evening', status_fn=fn)[0]


def test_a_failed_broadcast_fails_the_job(ledger, monkeypatch):
    """Interrogation finding (Codex): UNKNOWN after a send error is reported as a failed push, exit 1."""
    calls = []
    monkeypatch.setattr(push_to_line, '_recommendation_freshness_issue', lambda: (False, '2026-11-09', '2026-11-09'))
    monkeypatch.setattr(push_to_line, '_calendar_freshness', lambda *a: (True, '2026-11-09', ''))
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_start', lambda **kw: None)
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_finish', lambda **kw: calls.append(kw))
    monkeypatch.setattr(push_to_line, 'get_pipeline_baseline_date', lambda: '2026-11-09')

    def evening(dry_run=False):
        try:
            push_to_line.publish_once(push_to_line._PUBLISH_KEY, lambda: (_ for _ in ()).throw(RuntimeError('send failed')))
        except RuntimeError:
            pass  # _broadcast_flex prints and swallows
    monkeypatch.setattr(push_to_line, 'run_evening', evening)
    assert push_to_line.main(['--time', 'evening']) == 1
    assert calls[-1]['status'] == 'failed' and 'UNKNOWN' in calls[-1]['error_summary']


def test_a_conflicting_session_is_never_skipped_by_the_gate():
    """Adversarial review 2026-11-09: a CONFLICT day (exchange data and schedule disagree) stops the push instead of approving the day before."""
    status = lambda day: 'CONFLICT' if day.isoformat() == '2026-11-09' else ('CLOSED_OBSERVED' if day.weekday() >= 5 else 'EXPECTED_OPEN')
    ok, expected, reason = trading_calendar.publish_freshness('2026-11-06', '2026-11-06', date(2026, 11, 9), 'evening', status_fn=status)
    assert not ok and expected == '2026-11-09' and 'CONFLICT' in reason
