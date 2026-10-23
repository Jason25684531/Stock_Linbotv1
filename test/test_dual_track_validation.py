"""Download rules of scripts/dual_track_validation.py (OD-22): budget, release times, attempts, batch stop. No network, no DB."""
import json

import pytest

from scripts import dual_track_validation as dtv
from scripts import official_data_recovery as odr


@pytest.fixture(autouse=True)
def restore_reserve():
    yield
    odr.reserve = dtv._ORIGINAL_RESERVE


def test_budget_of_this_change_replaces_the_previous_default(tmp_path):
    ledger = tmp_path / 'l.jsonl'
    dtv.configure(budget=2)
    odr.reserve(ledger, 'T86', '2026-01-02', 'u')
    odr.reserve(ledger, 'T86', '2026-01-05', 'u')
    with pytest.raises(odr.BudgetExceeded):
        odr.reserve(ledger, 'T86', '2026-01-06', 'u')
    assert odr.sent_count(ledger) == 2 and dtv.BUDGET == 1900


def test_release_instant_is_logged_for_every_request(tmp_path):
    ledger = tmp_path / 'l.jsonl'
    gate = dtv.LoggedGate(ledger, interval=0)
    gate.context = {'dataset': 'T86', 'date': '2026-01-02', 'attempt': 1}
    gate.wait()
    gate.wait()
    rows = odr.ledger_rows(ledger)
    assert [r['event'] for r in rows] == ['RELEASED', 'RELEASED'] and rows[0]['dataset'] == 'T86' and 'ts' in rows[0]


def test_a_session_gets_at_most_three_attempts_and_only_transport_failures_are_retried(tmp_path, monkeypatch):
    calls = []
    outcomes = iter(['FAILED:ReadTimeout', 'REJECTED:ValueError', 'OK'])

    def fake(dataset, day, gate, ledger, trusted, trading=True):
        calls.append(day)
        return next(outcomes), None

    monkeypatch.setattr(odr, 'acquire_chip', fake)
    gate = dtv.LoggedGate(tmp_path / 'l.jsonl', interval=0)
    assert dtv.acquire_with_retries('T86', '2026-01-02', gate, tmp_path / 'l.jsonl', {})[0::2] == ('OK', 3)
    calls.clear()
    monkeypatch.setattr(odr, 'acquire_chip', lambda *a, **k: (calls.append(1), ('FAILED:X', None))[1])
    assert dtv.acquire_with_retries('T86', '2026-01-05', gate, tmp_path / 'l.jsonl', {})[2] == 3 and len(calls) == 3
    calls.clear()
    monkeypatch.setattr(odr, 'acquire_chip', lambda *a, **k: (calls.append(1), ('MISSING_ON_TRADING_DAY', None))[1])
    assert dtv.acquire_with_retries('T86', '2026-01-06', gate, tmp_path / 'l.jsonl', {})[2] == 1 and len(calls) == 1  # a data gap is recorded, not retried


def test_batch_stops_after_five_consecutive_failures(tmp_path, monkeypatch):
    seen = []

    def always_fail(dataset, day, gate, ledger, trusted):
        seen.append((dataset, day))
        return 'FAILED:X', None, 3

    monkeypatch.setattr(dtv, 'acquire_with_retries', always_fail)
    days = [f'2026-01-{d:02d}' for d in range(2, 12)]
    outcome = dtv.run_batch('2026Q1', days, None, tmp_path / 'l.jsonl', {})
    assert len(seen) == 5 and 'consecutive failures' in outcome['stopped'] and len(outcome['states']) == 5


def test_a_success_resets_the_failure_count(tmp_path, monkeypatch):
    sequence = iter(['FAILED:X'] * 4 + ['OK'] + ['FAILED:X'] * 4 + ['OK'] * 20)
    monkeypatch.setattr(dtv, 'acquire_with_retries', lambda *a: (next(sequence), None, 1))
    outcome = dtv.run_batch('2026Q1', [f'2026-01-{d:02d}' for d in range(2, 12)], None, tmp_path / 'l.jsonl', {})
    assert outcome['stopped'] is None and len(outcome['states']) == 20


def test_quarter_batches_cover_exactly_the_approved_range():
    batches = dtv.quarter_batches()
    days = [d for v in batches.values() for d in v]
    assert len(batches) == 16 and days[0] == '2022-01-03' and days[-1] <= '2025-12-12' and days == sorted(set(days))
