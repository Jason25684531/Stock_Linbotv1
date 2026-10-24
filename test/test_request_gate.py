"""Request spacing: one process, retries, and several processes sharing one state file (the 2026-10-23 ledger had one logged gap of 2.934 s)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.research.sources import twse

ROOT = Path(__file__).resolve().parents[1]


def fake_gate(**kwargs):
    now, sleeps = [0.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    return twse.RequestGate(3.2, clock=lambda: now[0], sleep=sleep, **kwargs), now, sleeps


def test_gap_is_measured_by_the_gate_itself():
    gate, now, sleeps = fake_gate()
    gate.wait()
    assert gate.last_gap is None
    now[0] += 1.0
    gate.wait()
    assert sleeps == [2.2] and gate.last_gap == pytest.approx(3.2)
    now[0] += 5.0  # a slow response: no sleeping, the real gap is reported
    gate.wait()
    assert sleeps == [2.2] and gate.last_gap == pytest.approx(5.0)


def test_every_retry_attempt_is_released_at_least_the_interval_apart(tmp_path, monkeypatch):
    from scripts import dual_track_validation as dtv

    ledger = tmp_path / 'ledger.jsonl'
    gate = dtv.LoggedGate(ledger)
    inner, now, _ = fake_gate()
    gate.gate = inner
    attempts = []

    def acquire_chip(dataset, day, gate_, ledger_, trusted):
        gate_.wait()  # the real acquire_chip waits once per request
        now[0] += 0.4  # response time
        attempts.append(gate_.context['attempt'])
        return ('FAILED_TRANSPORT', None) if len(attempts) < 3 else ('OK', None)

    monkeypatch.setattr(dtv.odr, 'acquire_chip', acquire_chip)
    state, _, used = dtv.acquire_with_retries('T86', '2022-10-07', gate, ledger, {})
    gaps = [json.loads(line)['gate_gap_seconds'] for line in ledger.read_text(encoding='utf-8').splitlines()]
    assert (state, used, attempts) == ('OK', 3, [1, 2, 3])
    assert gaps[0] is None and all(gap >= 3.2 for gap in gaps[1:])


CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.research.sources.twse import RequestGate
gate = RequestGate(float(sys.argv[3]), state_path=Path(sys.argv[2]))
for _ in range(int(sys.argv[4])):
    gate.wait()
    print(repr(gate.last_release_wall), flush=True)
"""


def test_processes_sharing_a_state_file_are_spaced_apart_together(tmp_path):
    interval, per_process, workers = 0.3, 4, 3
    state = tmp_path / 'gate.state'
    procs = [subprocess.Popen([sys.executable, '-c', CHILD, str(ROOT), str(state), str(interval), str(per_process)], stdout=subprocess.PIPE, text=True) for _ in range(workers)]
    stamps = sorted(float(line) for p in procs for line in p.communicate(timeout=60)[0].split())
    assert len(stamps) == workers * per_process
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    # ponytail: 0.02 s tolerance = the Windows time.time() tick (15.6 ms); the gate compares on the same clock, so no gap is below interval by more than one tick
    assert min(gaps) >= interval - 0.02, gaps


def test_without_a_state_file_behaviour_is_unchanged():
    gate, now, sleeps = fake_gate()
    assert gate.state_path is None
    gate.wait()
    now[0] += 0.5
    gate.wait()
    assert sleeps == [2.7]
