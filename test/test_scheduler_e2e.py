"""End-to-end scheduler tests: blocked, waiting, failing and interrupted tasks never stop the runnable ones. Local only, no requests."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from scripts import daily_runner as dr
from scripts import research_workflow as rw
from scripts import task_controller as tc

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)  # 10:00 Taipei
HASHES = {k: 'f' * 64 for k in rw.SIGNAL_HASHES}


def make(root, task_id, handler='h', status=None, **kw):
    tc.create(task_id, 'p', ['a'], 'agent', 'G', root=root, handler=handler, **kw)
    if status:
        tc.set_status(task_id, status, root)


def done(task, root):
    tc.set_status(task['task_id'], 'DONE', root)


def test_1_waiting_owner_does_not_stop_other_tasks(tmp_path):
    make(tmp_path, 'A-OWNER', status='WAITING_OWNER')
    make(tmp_path, 'B-VERIFY', status='VERIFYING')  # review-blocked
    make(tmp_path, 'C-DEP', depends_on=['B-VERIFY'])  # only its dependent waits
    make(tmp_path, 'D-FREE')
    state = tc.run_once({'h': done}, tmp_path, at=T0)
    assert state['ran'] == [('D-FREE', 'DONE')]
    assert [tc.load(t, tmp_path)['status'] for t in ('A-OWNER', 'B-VERIFY', 'C-DEP')] == ['WAITING_OWNER', 'VERIFYING', 'PENDING']
    classes = {r['task_id']: r['stop_class'] for r in tc.stop_report({'h': done}, tmp_path)}
    assert classes == {'A-OWNER': 'WAITING_OWNER', 'B-VERIFY': 'REVIEW_BLOCKED', 'C-DEP': 'NO_RUNNABLE_TASK'}


def test_2_waiting_data_is_not_retried_before_its_check_time(tmp_path):
    calls = []

    def needs_data(task, root):
        calls.append(1)
        tc.wait_data(task['task_id'], '2026-10-27', (T0 + timedelta(hours=5)).isoformat(), 'not published', root)

    make(tmp_path, 'W')
    assert tc.run_once({'h': needs_data}, tmp_path, at=T0)['ran'] == [('W', 'WAITING_DATA')]
    for minutes in (1, 30, 299):  # repeated triggers before the check time do nothing
        state = tc.run_once({'h': needs_data}, tmp_path, at=T0 + timedelta(minutes=minutes))
        assert state['state'] == 'IDLE_UNTIL_NEXT_EVENT' and state['next_wake'] == (T0 + timedelta(hours=5)).isoformat()
    assert len(calls) == 1
    tc.run_once({'h': needs_data}, tmp_path, at=T0 + timedelta(hours=5))
    assert len(calls) == 2


def test_3_failing_subagent_retries_with_backoff_then_stops_while_others_run(tmp_path):
    def broken(task, root):
        raise RuntimeError('subagent returned no evidence')

    make(tmp_path, 'F', handler='broken')
    make(tmp_path, 'G')
    state = tc.run_once({'broken': broken, 'h': done}, tmp_path, at=T0)
    assert dict(state['ran']) == {'F': 'FAILED_RETRYABLE', 'G': 'DONE'}
    assert tc.run_once({'broken': broken}, tmp_path, at=T0)['state'] == 'IDLE_UNTIL_NEXT_EVENT'  # backoff respected
    at = T0
    for _ in range(3):
        at += timedelta(days=1)
        tc.run_once({'broken': broken}, tmp_path, at=at)
    assert tc.load('F', tmp_path)['status'] == 'FAILED_FINAL'
    assert tc.run_once({'broken': broken}, tmp_path, at=at + timedelta(days=9))['ran'] == []


def test_4_interrupted_run_resumes_without_duplicate_writes(tmp_path):
    writes, crash = [], {'once': True}

    def two_steps(task, root):
        tc.step(task['task_id'], 'write-A', lambda: writes.append('A') or 'A', root)
        if crash.pop('once', False):
            raise KeyboardInterrupt('process killed') if False else RuntimeError('killed between steps')
        tc.step(task['task_id'], 'write-B', lambda: writes.append('B') or 'B', root)
        tc.set_status(task['task_id'], 'DONE', root)

    make(tmp_path, 'I')
    tc.run_once({'h': two_steps}, tmp_path, at=T0)
    assert tc.load('I', tmp_path)['status'] == 'FAILED_RETRYABLE'
    tc.run_once({'h': two_steps}, tmp_path, at=T0 + timedelta(hours=1))
    tc.run_once({'h': two_steps}, tmp_path, at=T0 + timedelta(hours=2))  # duplicate trigger after DONE
    assert writes == ['A', 'B'] and tc.load('I', tmp_path)['status'] == 'DONE'


def test_dev_work_gate_ignores_owner_and_data_waits(tmp_path):
    make(tmp_path, 'OWN', handler=None, status='WAITING_OWNER')
    make(tmp_path, 'DAILY')  # deterministic handler: Daily Runner territory
    assert tc.dev_work(tmp_path, T0) == []
    make(tmp_path, 'REVIEW', handler=None, status='VERIFYING')
    make(tmp_path, 'NEW', handler=None)
    assert tc.dev_work(tmp_path, T0) == ['NEW', 'REVIEW']


def _guard(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHAINED = ['cat x; rm .env', 'grep x .env && rm .env', 'ls outputs/task_state | xargs rm', 'git restore .', 'git stash drop']


def test_5_guard_denials_hold_in_the_owner_patch():
    guard = _guard(ROOT / 'docs/owner_patch/guard.py')
    allow_all = lambda op: {'operation': op}
    for command in CHAINED + ['git push', 'git -C . push', 'pip install x', 'echo x > .claude/owner_grants.json']:
        assert guard.check({'tool_name': 'Bash', 'tool_input': {'command': command}}, allow_all), command


def test_5_owner_patch_fetch_rule_ignores_mere_mentions():
    guard = _guard(ROOT / 'docs/owner_patch/guard.py')
    deny_all = lambda op: None
    assert guard.check({'tool_name': 'Bash', 'tool_input': {'command': 'grep -n MI_INDEX scripts/official_data_recovery.py'}}, deny_all) is None
    assert guard.check({'tool_name': 'Bash', 'tool_input': {'command': 'myenv/Scripts/python.exe scripts/t1b_fetch.py'}}, deny_all)


@pytest.mark.xfail(strict=True, reason='live guard keeps the chaining bypass until the Owner applies docs/owner_patch (strict: turns red once fixed)')
def test_5b_live_guard_still_has_the_chaining_bypass():
    guard = _guard(ROOT / '.claude/hooks/guard.py')
    assert all(guard.check({'tool_name': 'Bash', 'tool_input': {'command': c}}, lambda op: None) for c in CHAINED)


# ---------------------------------------------------------------- Daily Runner on a synthetic calendar

REAL_LEDGERS = [ROOT / 'outputs' / name / 'ledger.jsonl' for name in ('research_shadow', 'shadow_selection')]


def _digest(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


@pytest.fixture(autouse=True)
def real_ledgers_untouched():
    """Incident 2026-10-25: a test wrote into the real research ledger. Every test here now proves it did not."""
    before = [_digest(p) for p in REAL_LEDGERS]
    yield
    assert [_digest(p) for p in REAL_LEDGERS] == before, 'a test modified a real ledger'


@pytest.fixture
def calendar(monkeypatch, tmp_path):
    days = {d: (True, None, None) for d in pd.bdate_range(end='2026-10-22', periods=59)}
    closures = {pd.Timestamp('2026-10-23'): {'reason': 'typhoon'}, pd.Timestamp('2026-10-26'): {'reason': 'make-up holiday'}}
    monkeypatch.setattr(rw, 'load_sessions', lambda: days)
    monkeypatch.setattr(rw, 'load_closures', lambda: (closures, frozenset()))
    monkeypatch.setattr(rw, 'SHADOW', tmp_path / 'research_shadow')
    return days


def run(day, tmp_path, at, **kw):
    dr.ensure_task(day, tmp_path)
    return tc.run_once({'daily_shadow': lambda t, r: dr.daily_shadow(t, r, now=at, **kw)}, tmp_path, at=at)


def test_daily_closed_day_is_skipped(calendar, tmp_path):
    run('2026-10-26', tmp_path, T0)
    assert tc.load('T-DAILY-SHADOW-2026-10-26', tmp_path)['outcome'] == 'SKIPPED_CLOSED'


def test_daily_waits_for_the_close_then_needs_a_trusted_grant(calendar, tmp_path):
    run('2026-10-27', tmp_path, T0)  # 10:00 Taipei
    t = tc.load('T-DAILY-SHADOW-2026-10-27', tmp_path)
    assert t['status'] == 'WAITING_DATA' and t['check_after'] == '2026-10-27T07:00:00+00:00'
    untrusted = tmp_path / 'grants.json'  # writable by this account: never trusted
    untrusted.write_text('{"grants": [{"operation": "official_fetch", "expires_at": "2099-01-01T00:00:00+00:00", "scope": {"datasets": ["MI_INDEX"], "dates": ["2026-10-27"]}}]}')
    fetched = []
    run('2026-10-27', tmp_path, T0 + timedelta(hours=6), grants=untrusted, fetch=lambda d: fetched.append(d))
    t = tc.load('T-DAILY-SHADOW-2026-10-27', tmp_path)
    assert t['status'] == 'WAITING_OWNER' and 'not OS-protected' in t['blockers'][-1]['detail'] and fetched == []


def test_daily_missed_deadline_records_blocked_never_prospective(calendar, tmp_path):
    late = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
    run('2026-10-27', tmp_path, late)
    t = tc.load('T-DAILY-SHADOW-2026-10-27', tmp_path)
    from core.research.shadow_selection import _lines
    assert t['outcome'] == 'BLOCKED_DEADLINE' and [r['kind'] for r in _lines(rw.SHADOW)] == ['BLOCKED']
    run('2026-10-27', tmp_path, late + timedelta(hours=1))  # a second trigger adds nothing
    assert len(_lines(rw.SHADOW)) == 1


def test_daily_cached_session_is_recorded_once(calendar, monkeypatch, tmp_path):
    calendar[pd.Timestamp('2026-10-27')] = (True, None, None)
    monkeypatch.setattr(rw, 'cmd_audit', lambda: None)
    monkeypatch.setattr(rw, 'readiness', lambda *a, **k: {'blockers': []})
    monkeypatch.setattr(rw, 'cmd_shadow', lambda day: rw.record_research_day(day, [], {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}, root=rw.SHADOW))
    paper_calls = []
    monkeypatch.setattr(dr, 'paper_session', lambda day, days: paper_calls.append(day) or {'reconcile': 'PASS', 'status': 'ACTIVE'})
    at = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    run('2026-10-27', tmp_path, at)
    run('2026-10-27', tmp_path, at + timedelta(minutes=5))
    from core.research.shadow_selection import _lines, verify
    assert tc.load('T-DAILY-SHADOW-2026-10-27', tmp_path)['outcome'] == 'RECORDED'
    assert len(_lines(rw.SHADOW)) == 1 and verify(rw.SHADOW)['status'] == 'PASS'
    assert paper_calls == ['2026-10-27']  # the paper step ran once, after the signal


def test_paper_session_plans_on_the_first_day_and_fills_next_day(monkeypatch, tmp_path):
    from scripts import paper_trading as pt
    picks = [{'stock_id': f'S{i}'} for i in range(25)]
    bars = {f'S{i}': {'open': 50.0, 'high': 51.0, 'low': 49.0, 'close': 50.0, 'volume': 1e6, 'amount': 1e9, 'prior_amount': 1e9, 'limit_up': 55.0, 'limit_down': 45.0} for i in range(25)}
    monkeypatch.setattr(pt, 'session_bars', lambda day, days: (bars, {s: 50.0 for s in bars}))
    monkeypatch.setattr(rw, 'SHADOW', tmp_path / 'shadow')
    for day in ('2026-10-27', '2026-10-28'):
        rw.record_research_day(day, picks, {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES})
    days = {pd.Timestamp(d): (True, None, None) for d in ('2026-10-27', '2026-10-28')}
    first = dr.paper_session('2026-10-27', days, root=tmp_path / 'paper')
    assert first['pending'] == dr.PAPER_TOP and first['fills'] == {}
    second = dr.paper_session('2026-10-28', days, root=tmp_path / 'paper')
    assert second['fills'] == {'FILLED': dr.PAPER_TOP} and second['reconcile'] == 'PASS' and second['pending'] == 0  # not a rebalance day


def test_grant_inside_repo_is_never_trusted():
    assert not tc.grant_trusted(ROOT / '.claude/owner_grants.json')


def test_read_only_attribute_or_own_file_is_never_trusted(tmp_path):
    import os
    import stat
    path = tmp_path / 'grants.json'
    path.write_text('{"grants": []}')
    assert not tc.grant_trusted(path)  # owned and writable by this account
    os.chmod(path, stat.S_IREAD)  # read-only attribute: the agent could clear it again
    try:
        assert not tc.grant_trusted(path)
    finally:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def test_a_failed_request_is_never_repeated(calendar, monkeypatch, tmp_path):
    monkeypatch.setattr(tc, 'usable_grant', lambda *a, **k: ({'operation': 'official_fetch'}, ''))
    calls = []

    def flaky(day):
        calls.append(day)
        raise ConnectionError('TWSE busy')

    at = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)  # 16:00 Taipei, before the deadline
    run('2026-10-27', tmp_path, at, fetch=flaky)
    t = tc.load('T-DAILY-SHADOW-2026-10-27', tmp_path)
    assert t['status'] == 'WAITING_OWNER' and calls == ['2026-10-27']
    tc.set_status(t['task_id'], 'PENDING', tmp_path)  # even if someone reopens it, no second request
    run('2026-10-27', tmp_path, at + timedelta(minutes=30), fetch=flaky)
    assert calls == ['2026-10-27'] and 'one request per date' in tc.load(t['task_id'], tmp_path)['blockers'][-1]['detail']


def test_paper_session_halts_instead_of_guessing(monkeypatch, tmp_path):
    from scripts import paper_trading as pt
    monkeypatch.setattr(pt, 'session_bars', lambda day, days: ({}, {}))
    monkeypatch.setattr(rw, 'SHADOW', tmp_path / 'shadow')
    off_calendar = dr.paper_session('2026-10-26', {pd.Timestamp('2026-10-27'): (True, None, None)}, root=tmp_path / 'a')
    assert off_calendar['status'] == 'HALTED'
    no_signal = dr.paper_session('2026-10-27', {pd.Timestamp('2026-10-27'): (True, None, None)}, root=tmp_path / 'b')
    assert no_signal['status'] == 'HALTED' and 'no A191_132 signal' in pt.load(tmp_path / 'b')['halt_reason']


def test_daily_runner_through_the_real_cmd_shadow_writes_the_hash_contract(calendar, monkeypatch, tmp_path):
    """Compatibility: the Daily Runner calls rw.cmd_shadow(day) unchanged; the record it writes carries every version hash."""
    calendar[pd.Timestamp('2026-10-27')] = (True, None, None)
    monkeypatch.setattr(rw, 'cmd_audit', lambda: None)
    monkeypatch.setattr(rw, 'readiness', lambda *a, **k: {'blockers': []})
    monkeypatch.setattr(rw, 'a191_132_scores', lambda day, days: (pd.Series({'2330': 2.0, '2317': 1.0}), {'MI_INDEX_20261027.json': 'a' * 64}, []))
    monkeypatch.setattr(dr, 'paper_session', lambda day, days: {'reconcile': 'PASS', 'status': 'ACTIVE'})
    run('2026-10-27', tmp_path, datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc))
    from core.research.shadow_selection import _lines, verify
    (record,) = _lines(rw.SHADOW)
    assert tc.load('T-DAILY-SHADOW-2026-10-27', tmp_path)['outcome'] == 'RECORDED' and verify(rw.SHADOW)['status'] == 'PASS'
    assert all(len(record[k]) == 64 for k in ('code_sha256', 'config_sha256')) and record['strategy_version'] == rw.STRATEGY_VERSION
    assert record['inputs_sha256']['MI_INDEX_20261027.json'] == 'a' * 64 and [p['stock_id'] for p in record['picks']] == ['2330', '2317']
