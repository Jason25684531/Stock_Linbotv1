from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Event
from types import SimpleNamespace

import pytest

from core.research import oos_gate
from scripts import research_workflow as rw


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'trials.jsonl'
    rw.register({'hypothesis_id': 'H', 'strategy': 'S', 'hypothesis': 'h', 'falsification_test': 't',
                 'data_window': 'development', 'trial_budget': '1', 'source': 's'}, path=registry, today='2026-10-28')
    code, inputs, oos = tmp_path / 'code.py', tmp_path / 'input.csv', tmp_path / 'synthetic-oos.csv'
    code.write_text('code', encoding='utf-8')
    inputs.write_text('development', encoding='utf-8')
    oos.write_bytes(b'synthetic fixture')
    monkeypatch.setattr(rw, 'ARCHIVED_MANIFESTS', {})
    monkeypatch.setattr(oos_gate, 'REQUIRED_CODE', (code,))
    monkeypatch.setattr(oos_gate, 'REQUIRED_INPUTS', frozenset({'input'}))
    monkeypatch.setattr(oos_gate, '_require_oos_eligibility', lambda: None)
    evaluator = SimpleNamespace(evaluate_oos=lambda data, config: 'PASS')
    monkeypatch.setattr(oos_gate, 'import_module', lambda name: evaluator)
    config = {'period': 20}
    trial = rw.admit_experiment('H', config, [code], {'input': inputs}, '2025-12-31', registry=registry, ledger=ledger)
    path = oos_gate.freeze('S', trial['experiment_id'], {'input': inputs}, hashlib.sha256(oos.read_bytes()).hexdigest(),
                           directory=tmp_path / 'freeze', ledger=ledger, registry=registry)
    return dict(path=path, config=config, ledger=ledger, code=code, oos=oos, inputs=inputs,
                registry=registry, trial=trial, root=tmp_path, evaluator=evaluator)


def test_hash_drift_consumes_attempt_before_oos_read(frozen):
    frozen['code'].write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='CODE_HASH_DRIFT'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['root'] / 'missing-oos', ledger=frozen['ledger'])
    frozen['code'].write_text('code', encoding='utf-8')
    with pytest.raises(ValueError, match='OOS_ALREADY_ATTEMPTED'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['oos'], ledger=frozen['ledger'])
    with pytest.raises(ValueError, match='HYPOTHESIS_ALREADY_FROZEN'):
        oos_gate.freeze('S', frozen['trial']['experiment_id'], {'input': frozen['inputs']}, 'f' * 64,
                        directory=frozen['root'] / 'other', ledger=frozen['ledger'], registry=frozen['registry'])
    assert rw.verify_trials(frozen['ledger']) == 'PASS' and rw.effective_trials(frozen['ledger']) == 744


def test_concurrent_attempt_is_reserved_before_reader(frozen):
    entered, release = Event(), Event()
    def reader(data, config):
        assert data == b'synthetic fixture'
        entered.set()
        assert release.wait(5)
        return 'PASS'
    def run():
        return oos_gate.evaluate(frozen['path'], frozen['config'], frozen['oos'], ledger=frozen['ledger'])
    frozen['evaluator'].evaluate_oos = reader
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        assert entered.wait(5)
        try:
            with pytest.raises(ValueError, match='OOS_ALREADY_ATTEMPTED'):
                run()
        finally:
            release.set()
        assert future.result() == 'PASS'
    rows = rw._trial_rows(frozen['ledger'])
    assert [row['kind'] for row in rows] == ['TRIAL', 'OOS_FREEZE', 'OOS_EVALUATION', 'OOS_RESULT']
    assert rows[-1]['outcome'] == 'PASS' and rw.verify_trials(frozen['ledger']) == 'PASS'


def test_missing_freeze_does_not_read_oos(tmp_path, monkeypatch):
    monkeypatch.setattr(oos_gate, '_require_oos_eligibility', lambda: None)
    with pytest.raises(FileNotFoundError):
        oos_gate.evaluate(tmp_path / 'missing-freeze', {}, tmp_path / 'missing-oos', ledger=tmp_path / 'ledger')


def test_unverified_qualification_refuses_freeze_without_side_effects(tmp_path):
    with pytest.raises(PermissionError, match='OOS_QUALIFICATION_EVIDENCE_UNVERIFIED'):
        oos_gate.freeze('S', 'trial', {}, 'a' * 64, directory=tmp_path / 'freeze', ledger=tmp_path / 'ledger')
    assert not (tmp_path / 'freeze').exists() and not (tmp_path / 'ledger').exists()


def test_reader_failure_cannot_be_retried(frozen):
    def fail(_, config):
        raise RuntimeError('evaluation failed')
    frozen['evaluator'].evaluate_oos = fail
    with pytest.raises(RuntimeError, match='evaluation failed'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['oos'], ledger=frozen['ledger'])
    with pytest.raises(ValueError, match='OOS_ALREADY_ATTEMPTED'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['oos'], ledger=frozen['ledger'])
    last = rw._trial_rows(frozen['ledger'])[-1]
    assert last['outcome'] == 'FAILED_OR_INTERRUPTED' and last['reason'] == 'RuntimeError: evaluation failed'


def test_production_mode_never_reserves_or_reads(frozen, monkeypatch):
    from core.runtime import operation_policy as policy
    monkeypatch.setattr(policy, 'current_mode', lambda: policy.ExecutionMode.PRODUCTION)
    before = frozen['ledger'].read_bytes()
    with pytest.raises(PermissionError, match='RESEARCH_MODE_REQUIRED'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['oos'], ledger=frozen['ledger'])
    assert frozen['ledger'].read_bytes() == before


def test_missing_fixed_evaluator_fails_before_oos_read(frozen):
    del frozen['evaluator'].evaluate_oos
    with pytest.raises(ValueError, match='OOS_EVALUATOR_UNAVAILABLE'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['root'] / 'missing-oos', ledger=frozen['ledger'])
    with pytest.raises(ValueError, match='OOS_ALREADY_ATTEMPTED'):
        oos_gate.evaluate(frozen['path'], frozen['config'], frozen['oos'], ledger=frozen['ledger'])
