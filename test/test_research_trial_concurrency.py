from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts import research_workflow as rw


def test_concurrent_admission_and_interrupted_head_fail_closed(tmp_path, monkeypatch):
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'trials.jsonl'
    rw.register({'hypothesis_id': 'H', 'strategy': 'S', 'hypothesis': 'h', 'falsification_test': 't',
                 'data_window': 'dev', 'trial_budget': '1', 'source': 's'}, path=registry, today='2026-10-28')
    code, data = tmp_path / 'code.py', tmp_path / 'data.csv'
    code.write_text('code', encoding='utf-8')
    data.write_text('data', encoding='utf-8')
    monkeypatch.setattr(rw, 'ARCHIVED_MANIFESTS', {})
    def admit():
        return rw.admit_experiment('H', {'config_id': 'new'}, [code], {'bars': data}, '2025-12-31',
                                   registry=registry, ledger=ledger)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: admit(), range(2)))
    assert sorted(row['kind'] for row in rows) == ['REPLAY', 'TRIAL']
    assert rw.verify_trials(ledger) == 'PASS' and rw.effective_trials(ledger) == 744
    monkeypatch.setattr(rw.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OSError, match='interrupted'):
        admit()
    assert rw.verify_trials(ledger) == 'FAIL'
    with pytest.raises(ValueError, match='does not verify'):
        admit()
