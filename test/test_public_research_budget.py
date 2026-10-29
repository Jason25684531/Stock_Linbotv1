import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from core.runtime import public_data as public
from core.runtime import research_budget as rb


def reference_context(name='TWTAUU'):
    return public.context(f'https://www.twse.com.tw/rwd/zh/{"reducation" if name == "TWTAUU" else "change"}/{name}',
                          params={'startDate': '20220101', 'endDate': '20261023', 'response': 'json'})


def test_migration_is_idempotent_and_never_credits_history(public_budget):
    assert rb.status()['used'] == 7
    assert rb.status()['jobs']['od_p6']['remaining'] == 382
    assert rb.status()['jobs']['t1b']['remaining'] == 6
    rb.initialize()
    assert rb.status()['used'] == 7
    with pytest.raises(rb.BudgetDenied, match='BUDGET_HISTORY_UNKNOWN'):
        rb.check('calendar_2026')
    with pytest.raises(rb.BudgetDenied, match='BUDGET_UNASSIGNED'):
        rb.check(None)


def test_environment_can_only_lower_the_hard_cap(public_budget, monkeypatch):
    monkeypatch.setenv(rb.MAX_REQUESTS_ENV, '1000000')
    ctx = reference_context()
    for _ in range(6):
        attempt = rb.reserve(ctx)
        rb.finish(attempt, outcome='FAILED')
    with pytest.raises(rb.BudgetDenied, match='EXHAUSTED'):
        rb.reserve(ctx)
    assert rb.status()['jobs']['t1b']['used'] == 10


def test_uninitialized_or_lost_ledger_never_resets(public_budget):
    db, anchor = rb._paths()
    db.rename(db.with_suffix('.saved'))  # simulated lost state, no real ledger deletion
    with pytest.raises(rb.BudgetDenied, match='STATE_INVALID'):
        rb.initialize()
    assert anchor.exists()


def test_corrupt_state_and_partial_initialization_fail_closed(public_budget):
    db, _ = rb._paths()
    db.write_bytes(b'not a SQLite database')
    with pytest.raises(rb.BudgetDenied, match='STATE_INVALID'):
        rb.check('t1b')


def test_unknown_legacy_usage_does_not_initialize(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, 'LEGACY', {'od_p6': (tmp_path / 'missing.jsonl', 3)})
    with pytest.raises(rb.BudgetDenied, match='HISTORY_UNKNOWN'):
        rb.initialize()
    assert not rb._paths()[0].exists()


def test_invalid_legacy_rows_do_not_initialize(public_budget, tmp_path, monkeypatch):
    monkeypatch.setattr(rb, 'STATE', tmp_path / 'another-state')
    path = tmp_path / 'malformed.jsonl'
    path.write_text('{"event":"SENT"}\n')
    monkeypatch.setattr(rb, 'LEGACY', {'t1b': (path, 1)})
    with pytest.raises(rb.BudgetDenied, match='HISTORY_UNKNOWN'):
        rb.initialize()


def test_subprocesses_cannot_both_reserve_the_last_attempt(public_budget):
    ctx = reference_context()
    for _ in range(5):
        rb.finish(rb.reserve(ctx), outcome='FAILED')
    script = '''
import sys
from pathlib import Path
from core.runtime import research_budget as rb, public_data as public
rb.STATE=Path(sys.argv[1])
ctx=public.context('https://www.twse.com.tw/rwd/zh/reducation/TWTAUU',params={'startDate':'20220101','endDate':'20261023','response':'json'})
try:
    rb.reserve(ctx)
    print('RESERVED')
except rb.BudgetDenied:
    print('DENIED')
'''
    processes = [subprocess.Popen([sys.executable, '-c', script, str(rb.STATE)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=25)
        assert process.returncode == 0, stderr
        outcomes.append(stdout.strip())
    assert outcomes.count('RESERVED') == 1 and outcomes.count('DENIED') == 3
    assert rb.status()['jobs']['t1b']['used'] == 10


def test_reserved_attempt_survives_process_restart_without_refund(public_budget):
    rb.reserve(reference_context())
    result = subprocess.run([sys.executable, '-c',
                             'import sys,json; from pathlib import Path; from core.runtime import research_budget as rb; rb.STATE=Path(sys.argv[1]); print(json.dumps(rb.status()))',
                             str(rb.STATE)], capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(result.stdout)['jobs']['t1b']['used'] == 5
    with pytest.raises(rb.BudgetDenied, match='ALREADY_RESERVED'):
        rb.reserve(reference_context())


def test_transaction_lock_timeout_is_a_refusal(public_budget):
    db, _ = rb._paths()
    with sqlite3.connect(db, isolation_level=None) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(rb.BudgetDenied, match='STATE_INVALID'):
            rb.reserve(reference_context())
        blocker.execute('ROLLBACK')
    assert rb.status()['used'] == 7
