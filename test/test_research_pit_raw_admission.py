import csv
import hashlib

import pytest

from scripts import research_workflow as rw


def test_new_model_requires_complete_identity_and_counts_even_archived_parameters(tmp_path, monkeypatch):
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'trials.jsonl'
    rw.register({'hypothesis_id': rw.PIT_RAW_HYPOTHESIS, 'strategy': 'A191_132', 'hypothesis': 'h',
                 'falsification_test': 't', 'data_window': 'dev', 'trial_budget': '12', 'source': 's'},
                path=registry, today='2026-10-28')
    code = [tmp_path / 'runner.py', tmp_path / 'targets.py']
    for path in code:
        path.write_text(path.name, encoding='utf-8')
    inputs = {name: tmp_path / (name + '.csv') for name in ('bars', 'actions', 'evidence', 'scores', 'universe')}
    for path in inputs.values():
        path.write_text(path.name, encoding='utf-8')
    monkeypatch.setattr(rw, 'PIT_RAW_CODE', tuple(code))
    monkeypatch.setattr(rw, '_loaded_local_code', lambda: set())
    manifest = tmp_path / 'archive.csv'
    config = {'config_id': 'c1', 'factor_set': 'A191_132', 'factor_weighting': 'equal', 'top_n': 10,
              'rebalance_days': 20, 'portfolio_weighting': 'equal', 'config_hash': 'fixed'}
    with manifest.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['strategy_id', *rw.ARCHIVED_PARAMETERS])
        writer.writeheader()
        writer.writerow({'strategy_id': 'c1', **{key: config[key] for key in rw.ARCHIVED_PARAMETERS}})
    monkeypatch.setattr(rw, 'ARCHIVED_MANIFESTS', {manifest: hashlib.sha256(manifest.read_bytes()).hexdigest()})
    def admit(paths, data):
        return rw.admit_experiment(rw.PIT_RAW_HYPOTHESIS, config, paths, data, '2025-12-31', registry=registry, ledger=ledger)
    with pytest.raises(ValueError, match='PIT_RAW_CODE_INCOMPLETE'):
        admit(code[:1], inputs)
    with pytest.raises(ValueError, match='PIT_RAW_INPUTS_INCOMPLETE'):
        admit(code, {'bars': inputs['bars']})
    extra = tmp_path / 'loaded_dependency.py'
    extra.write_text('dependency', encoding='utf-8')
    monkeypatch.setattr(rw, '_loaded_local_code', lambda: {extra})
    with pytest.raises(ValueError, match='PIT_RAW_CODE_INCOMPLETE'):
        admit(code, inputs)
    assert rw.verify_trials(ledger) == 'EMPTY'
    row = admit([*code, extra], inputs)
    assert row['kind'] == 'TRIAL' and row['archived_config'] and row['counts_toward_dsr']
    assert rw.effective_trials(ledger) == 744
    assert admit([*code, extra], inputs)['kind'] == 'PIT_RAW_REPLAY'
    assert rw.effective_trials(ledger) == 744
