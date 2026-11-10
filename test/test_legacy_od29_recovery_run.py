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


def test_new_recovery_run_saves_version_2_evidence_against_an_isolated_ledger(tmp_path, monkeypatch):
    """Synthetic data, temporary registry, ledger and Run Store: the formal ledger and store are never touched."""
    import functools
    from core.backtest import run_store
    from scripts import research_workflow as rw
    from test_legacy_baseline_run import synthetic_inputs
    sid = 'quality_value_low_volatility'
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'trial_ledger.jsonl'
    rw.register({'hypothesis_id': recovery.hypothesis(sid), 'strategy': sid, 'hypothesis': 'synthetic', 'falsification_test': 'x',
                 'data_window': '2023-01-03..2025-12-31', 'trial_budget': '1', 'source': 'test'}, path=registry)
    inputs = {}
    for name in ('bars', 'financial_statements', 'monthly_revenue', 'publication_dates', 'corporate_actions', 'tri_source', 'tri_calendar'):
        inputs[name] = tmp_path / f'{name}.txt'
        inputs[name].write_text(name, encoding='utf-8')
    real_run, real_rows, real_admit = recovery.legacy.run_legacy, rw._trial_rows, rw.admit_experiment
    monkeypatch.setattr(recovery, 'OUT', tmp_path / 'out')
    monkeypatch.setattr(run_store, 'ROOT', tmp_path / 'runs')
    monkeypatch.setattr(recovery.legacy, 'input_paths', lambda: inputs)
    monkeypatch.setattr(recovery.legacy, 'run_legacy', lambda s: real_run(s, start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs))
    monkeypatch.setattr(recovery.workflow, 'admit_experiment', functools.partial(real_admit, registry=registry, ledger=ledger))
    monkeypatch.setattr(recovery.workflow, '_trial_rows', lambda _: real_rows(ledger))
    monkeypatch.setattr(recovery.workflow, 'record_result', lambda *a, **k: None)
    result = recovery.run_one(sid, None, 'synthetic benchmark unavailable')
    assert result['status'] == 'SUCCESS', result.get('reason')
    loaded = run_store.load_run(result['run_id'])
    assert {'positions_daily.csv', 'trades_stress.csv', 'open_positions.csv', 'costs.json', 'valuation_events.csv', 'pit_detail.csv',
            'health.json'} <= set(loaded['files']) and loaded['run']['evidence_version'] == 2 and loaded['run']['admission'] == 'TRIAL'
    row = next(r for r in real_rows(ledger) if r['experiment_id'] == result['experiment_id'])
    status = run_store.evidence_status(loaded, row, rw.verify_trials(ledger) == 'PASS', rw._tree_sha)
    assert status['status'] == 'ADMISSION_VERIFIED' and status['reproducible_offline'], status
