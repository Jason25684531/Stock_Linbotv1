from scripts import legacy_od29_recovery_run as recovery


def test_admitted_failure_is_saved_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, 'OUT', tmp_path)
    monkeypatch.setattr(recovery.workflow, '_trial_rows', lambda _: [])
    monkeypatch.setattr(recovery.workflow, 'admit_experiment', lambda *a, **k: {
        'experiment_id': 'trial', 'kind': 'TRIAL', 'inputs_sha256': {}, 'code_sha256': {}})
    monkeypatch.setattr(recovery.legacy, 'run_legacy', lambda sid: (_ for _ in ()).throw(ValueError('VALUATION_BLOCKED')))
    monkeypatch.setattr(recovery.common, 'save_failed', lambda row: 'failed-run')
    result_file = tmp_path / 'hypothesis_results.csv'
    monkeypatch.setattr(recovery.workflow, 'results_path', lambda _: result_file)
    recorded = []
    monkeypatch.setattr(recovery.workflow, 'record_result', lambda *a, **k: recorded.append(a))
    result = recovery.run_one(recovery.STRATEGIES[0], None, 'unavailable')
    assert result['status'] == 'FAILED' and result['failure_run_id'] == 'failed-run'
    assert 'VALUATION_BLOCKED' in result['reason'] and recorded[0][1] == 'FAILED'
    result_file.write_text('hypothesis_id\n' + result['hypothesis_id'] + '\n', encoding='utf-8')
    from core.backtest import run_store
    monkeypatch.setattr(run_store, 'load_run', lambda _: {'run': {'trial_id': 'trial', 'status': 'FAILED',
                                                                   'hypothesis_id': result['hypothesis_id'],
                                                                   'strategy_id': recovery.STRATEGIES[0]}})
    assert recovery.run_one(recovery.STRATEGIES[0], None, 'unavailable') == result


def test_previous_orchestrator_retains_admission_on_engine_exception(tmp_path, monkeypatch):
    old = recovery.common
    monkeypatch.setattr(old, 'ITEMS', tmp_path)
    monkeypatch.setattr(old.lab, 'admit', lambda *a, **k: {'experiment_id': 'trial', 'kind': 'TRIAL'})
    monkeypatch.setattr(old.legacy, 'run_legacy', lambda *a, **k: (_ for _ in ()).throw(ValueError('engine failed')))
    saved = []
    monkeypatch.setattr(old, 'save_failed', lambda evidence: saved.append(evidence) or 'failed-run')
    item = {'item_id': 'LEGACY:mean_reversion', 'strategy_id': 'mean_reversion', 'kind': 'legacy_baseline'}
    result = old.run_item(item, None, 'unavailable')
    assert result['status'] == 'FAILED' and result['experiment_id'] == 'trial'
    assert result['failure_run_id'] == 'failed-run' and saved[0]['experiment_id'] == 'trial'


def test_lab_orchestrator_retains_admission_on_engine_exception(tmp_path, monkeypatch):
    old = recovery.common
    monkeypatch.setattr(old, 'ITEMS', tmp_path)
    monkeypatch.setattr(old.lab, 'score_rows', lambda *a, **k: ([], {}))
    monkeypatch.setattr(old.lab, 'run_config', lambda item: {})
    monkeypatch.setattr(old.lab, 'admit', lambda *a, **k: {'experiment_id': 'lab-trial', 'kind': 'TRIAL'})
    monkeypatch.setattr(old.lab, 'simulate', lambda *a, **k: (_ for _ in ()).throw(ValueError('engine failed')))
    monkeypatch.setattr(old, 'save_failed', lambda evidence: 'failed-run')
    result = old.run_item({'item_id': 'LAB:c1', 'kind': 'single'}, None, 'unavailable')
    assert result['status'] == 'FAILED' and result['experiment_id'] == 'lab-trial' and result['failure_run_id'] == 'failed-run'


def test_failure_store_error_keeps_evidence(tmp_path, monkeypatch):
    old = recovery.common
    monkeypatch.setattr(old, 'ITEMS', tmp_path)
    monkeypatch.setattr(old, 'run_legacy_item', lambda *a: {'status': 'FAILED', 'experiment_id': 'trial'})
    monkeypatch.setattr(old, 'save_failed', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    result = old.run_item({'item_id': 'LEGACY:mean_reversion', 'strategy_id': 'mean_reversion', 'kind': 'legacy_baseline'}, None, 'unavailable')
    assert result['save_status'].startswith('SAVE_FAILED')
    assert (tmp_path / 'LEGACY_mean_reversion.json').is_file()


def test_interrupted_result_is_verified_and_recorded_once(tmp_path, monkeypatch):
    import json
    from core.backtest import run_store
    sid = recovery.STRATEGIES[0]
    hid = recovery.hypothesis(sid)
    monkeypatch.setattr(recovery, 'OUT', tmp_path)
    path = tmp_path / f'{sid}.json'
    path.write_text(json.dumps({'status': 'SUCCESS', 'hypothesis_id': hid, 'experiment_id': 't', 'run_id': 'r'}), encoding='utf-8')
    result_file = tmp_path / 'results.csv'
    monkeypatch.setattr(recovery.workflow, 'results_path', lambda _: result_file)
    monkeypatch.setattr(run_store, 'load_run', lambda _: {'run': {'status': 'SUCCESS', 'hypothesis_id': hid, 'trial_id': 't', 'strategy_id': sid}})
    calls = []
    def record(*args, **kwargs):
        calls.append(args)
        result_file.write_text('hypothesis_id\n' + hid + '\n', encoding='utf-8')
    monkeypatch.setattr(recovery.workflow, 'record_result', record)
    recovery.run_one(sid, None, '')
    recovery.run_one(sid, None, '')
    assert len(calls) == 1
    monkeypatch.setattr(run_store, 'load_run', lambda _: {'run': {'status': 'SUCCESS', 'hypothesis_id': hid, 'trial_id': 'wrong', 'strategy_id': sid}})
    import pytest
    with pytest.raises(ValueError, match='RESULT_RUN_MISMATCH'):
        recovery.run_one(sid, None, '')
