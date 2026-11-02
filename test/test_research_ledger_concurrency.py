"""Research ledger reads and appends are serialized across processes (Windows msvcrt locks included)."""
import hashlib
import multiprocessing as mp
import shutil
import time

import pytest

from core.research import research_mode
from scripts import research_workflow as workflow

WRITERS, PER_WRITER, READERS, READS = 4, 25, 3, 400  # load at which unlocked readers failed 14 times (negative control)


def _write(writer, registry, ledger, source):
    for i in range(PER_WRITER):
        workflow.admit_research_run('A', {'writer': writer, 'i': i}, research_mode.RESEARCH_ASSUMPTIONS_V6, [source],
                                    {'input': source}, '2025-12-31', registry=registry, ledger=ledger)


def _read(ledger, formal, errors):
    for _ in range(READS):
        try:
            workflow.research_trials(ledger)
            assert workflow.effective_trials(formal) == 743
        except Exception as exc:  # any reader failure is the race this test exists for
            errors.put(f'{type(exc).__name__}: {exc}')


def _setup(tmp_path):
    registry, ledger, formal, source = (tmp_path / n for n in ('registry.csv', 'research.jsonl', 'formal.jsonl', 'input.json'))
    for src, dst in ((workflow.TRIAL_LEDGER, formal), (workflow._trial_head(workflow.TRIAL_LEDGER), workflow._trial_head(formal))):
        shutil.copyfile(src, dst)  # a copy of the real formal ledger: read here, never the original
    workflow.register(research_mode.screen_registration(), path=registry)
    workflow.register(research_mode.assumptions_registration('V6'), path=registry)
    source.write_text('{}', encoding='utf-8')
    return registry, ledger, formal, source


def test_concurrent_admits_and_reads_keep_the_chain(tmp_path):
    registry, ledger, formal, source = _setup(tmp_path)
    formal_sha = hashlib.sha256(formal.read_bytes()).hexdigest()
    context = mp.get_context('spawn')
    errors = context.Queue()
    procs = [context.Process(target=_write, args=(w, str(registry), str(ledger), str(source))) for w in range(WRITERS)]
    procs += [context.Process(target=_read, args=(str(ledger), str(formal), errors)) for _ in range(READERS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(180)
    assert [p.exitcode for p in procs] == [0] * len(procs)
    found = []
    while not errors.empty():
        found.append(errors.get())
    assert found == []
    assert workflow.verify_trials(ledger) == 'PASS'
    assert workflow.research_trials(ledger) == WRITERS * PER_WRITER
    assert workflow.effective_trials(formal) == 743
    assert hashlib.sha256(formal.read_bytes()).hexdigest() == formal_sha  # the formal copy is never written


def test_nested_read_inside_the_lock_does_not_self_deadlock(tmp_path):
    _, ledger, _, _ = _setup(tmp_path)
    started = time.monotonic()
    with workflow._ledger_lock(ledger):
        assert workflow.research_trials(ledger) == 0
        assert workflow.verify_trials(ledger) == 'EMPTY'
    assert time.monotonic() - started < 1


def test_broken_chain_raises_once_and_lock_timeouts_retry(tmp_path, monkeypatch):
    registry, ledger, _, source = _setup(tmp_path)
    for i in range(2):
        workflow.admit_research_run('A', {'i': i}, research_mode.RESEARCH_ASSUMPTIONS_V6, [source], {'input': source},
                                    '2025-12-31', registry=registry, ledger=ledger)
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    from core.research.sources import twse
    real, calls = twse._exclusive, []

    def flaky(path, timeout=5.0):
        calls.append(path)
        if len(calls) < workflow.LOCK_ATTEMPTS:
            raise TimeoutError('busy')
        return real(path, timeout)
    monkeypatch.setattr(twse, '_exclusive', flaky)
    assert workflow.research_trials(ledger) == 2 and len(calls) == workflow.LOCK_ATTEMPTS
    monkeypatch.setattr(twse, '_exclusive', real)
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before
    ledger.write_text(ledger.read_text(encoding='utf-8').replace('"i": 1', '"i": 9'), encoding='utf-8')
    started = time.monotonic()
    with pytest.raises(ValueError, match='RESEARCH_LEDGER_UNVERIFIED'):
        workflow.research_trials(ledger)
    assert time.monotonic() - started < 1  # a bad chain is not a lock timeout: no retry, no wait
