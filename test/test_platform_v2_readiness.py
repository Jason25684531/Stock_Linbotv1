import pytest

from core.research.platform.readiness import build_readiness


@pytest.fixture(autouse=True)
def verified_run(monkeypatch):
    from core.backtest import run_store
    monkeypatch.setattr(run_store, 'load_run', lambda *args, **kwargs: {'run': {'period': ['2020-01-01', '2025-12-31']}})


def inputs():
    catalog = [{'strategy_id': f's{i}', 'family': 'native'} for i in range(19)]
    catalog += [{'strategy_id': 'fundamental', 'family': 'fundamental'}, {'strategy_id': 'h4', 'family': 'research'}]
    plan = [{'strategy_id': c['strategy_id'], 'state': 'REUSE', 'reason': 'IDENTICAL_RUN_IDENTITY', 'window': ['2020-01-01', '2025-12-31']} for c in catalog]
    plan[0].update(state='DATA_BLOCKED', reason='missing PIT')
    plan[-1].update(state='GOVERNED_ONLY', reason='sealed')
    results = {c['strategy_id']: {'status': 'ANALYZED', 'year_status': {'2020': 'PARTIAL', '2021': 'VERIFIED'}, 'run': {'identity_status': 'CURRENT'}} for c in catalog}
    return catalog, plan, results


def test_catalog_and_governance_are_preserved():
    rows = build_readiness(*inputs())
    assert len(rows) == 21
    assert rows[0]['state'] == 'DATA_BLOCKED' and not rows[0]['web_runnable']
    assert rows[-1]['state'] == 'GOVERNED_ONLY' and not rows[-1]['web_runnable']
    assert rows[1]['year_status'] == {'2020': 'PARTIAL', '2021': 'VERIFIED'}
    assert rows[1]['trusted_years'] == [2021]
    assert rows[1]['backtest_window'] == ['2020-01-01', '2025-12-31']
    assert rows[1]['data_eligible'] is True
    assert rows[1]['web_runnable'] is False
    assert rows[1]['reason'] == 'V2_WEB_NOT_READY'


def test_web_runnable_needs_a_publishable_v2_baseline():
    catalog, plan, results = inputs()
    rows = build_readiness(catalog, plan, results, v2={'s1': True, 's2': False})
    assert rows[1]['web_runnable'] is True and rows[1]['reason'] is None
    assert rows[2]['web_runnable'] is False and rows[2]['reason'] == 'V2_BASELINE_NOT_PUBLISHABLE'
    assert rows[3]['web_runnable'] is False and rows[3]['reason'] == 'V2_BASELINE_MISSING'
    assert rows[0]['web_runnable'] is False and rows[0]['state'] == 'DATA_BLOCKED'


def test_missing_or_stale_evidence_fails_closed():
    catalog, plan, results = inputs()
    del results['s1']
    results['s2']['run']['identity_status'] = 'IDENTITY_STALE'
    rows = build_readiness(catalog, plan, results)
    assert not rows[1]['web_runnable'] and rows[1]['reason'] == 'V1_RESULT_MISSING'
    assert not rows[2]['web_runnable'] and rows[2]['reason'] == 'IDENTITY_STALE'
    assert rows[2]['trusted_years'] == []


def test_inconsistent_catalog_refused():
    catalog, plan, results = inputs()
    plan.pop()
    with pytest.raises(ValueError, match='catalog'):
        build_readiness(catalog, plan, results)


def test_trust_is_recomputed_and_mismatches_fail_closed(monkeypatch):
    from core.backtest.run_store import canonical_hash
    from scripts import platform_v2_readiness as report
    inputs = {'snapshot_id': 'snap', 'evaluation_id': 'eval', 'code': {'a': 'hash'},
              'run_id': 'r1', 'manifest_sha256': 'manifest', 'identity_status': 'CURRENT', 'state': 'REUSE'}
    result = {'strategy_id': 's', 'identity_inputs': inputs,
              'identity': 'SI1-' + canonical_hash(inputs)[:16],
              'year_status': {'2021': 'VERIFIED'},
              'run': {'identity_status': 'CURRENT', 'run_id': 'r1'}}
    context = {'evaluation': {'snapshot_id': 'snap', 'evaluation_id': 'eval'},
               'code': {'a': 'hash'}}
    decision = {'state': 'REUSE', 'run_id': 'r1'}
    monkeypatch.setattr(report.strategy_intelligence, 'pick_run', lambda *args: ('r1', 'CURRENT'))
    monkeypatch.setattr(report.strategy_intelligence, 'load_run', lambda _: {'manifest_sha256': 'manifest'})
    monkeypatch.setattr(report.strategy_intelligence, 'trust_for', lambda *args: ({2021: 'PARTIAL'}, {}))
    verified = report.verify_result('s', result, decision, context)
    assert verified['year_status'] == {}
    assert verified['run']['identity_status'] == 'IDENTITY_STALE'


def test_matching_v1_recalculation_retains_trust(monkeypatch):
    from core.backtest.run_store import canonical_hash
    from scripts import platform_v2_readiness as report
    ident = {'snapshot_id': 'snap', 'evaluation_id': 'eval', 'code': {'a': 'hash'},
             'run_id': 'r1', 'manifest_sha256': 'manifest', 'identity_status': 'CURRENT', 'state': 'REUSE'}
    result = {'strategy_id': 's', 'identity_inputs': ident,
              'identity': 'SI1-' + canonical_hash(ident)[:16], 'year_status': {'2021': 'VERIFIED'},
              'run': {'identity_status': 'CURRENT', 'run_id': 'r1'}}
    context = {'evaluation': {'snapshot_id': 'snap', 'evaluation_id': 'eval'}, 'code': {'a': 'hash'}}
    monkeypatch.setattr(report.strategy_intelligence, 'pick_run', lambda *args: ('r1', 'CURRENT'))
    monkeypatch.setattr(report.strategy_intelligence, 'load_run', lambda _: {'manifest_sha256': 'manifest'})
    monkeypatch.setattr(report.strategy_intelligence, 'trust_for', lambda *args: ({2021: 'VERIFIED'}, {}))
    assert report.verify_result('s', result, {'state': 'REUSE', 'run_id': 'r1'}, context)['year_status'] == {'2021': 'VERIFIED'}
    result['identity_inputs']['evaluation_id'] = 'tampered'
    assert report.verify_result('s', result, {'state': 'REUSE', 'run_id': 'r1'}, context)['run']['identity_status'] == 'IDENTITY_STALE'


def test_result_run_cannot_disagree_with_verified_identity(monkeypatch):
    from core.backtest.run_store import canonical_hash
    from scripts import platform_v2_readiness as report
    ident = {'snapshot_id': 'snap', 'evaluation_id': 'eval', 'code': {}, 'state': 'REUSE',
             'run_id': 'r1', 'manifest_sha256': 'manifest', 'identity_status': 'CURRENT'}
    result = {'strategy_id': 's', 'identity_inputs': ident, 'identity': 'SI1-' + canonical_hash(ident)[:16],
              'year_status': {'2021': 'VERIFIED'}, 'run': {'run_id': 'other', 'identity_status': 'CURRENT'}}
    context = {'evaluation': {'snapshot_id': 'snap', 'evaluation_id': 'eval'}, 'code': {}}
    monkeypatch.setattr(report.strategy_intelligence, 'pick_run', lambda *args: ('r1', 'CURRENT'))
    monkeypatch.setattr(report.strategy_intelligence, 'load_run', lambda _: {'manifest_sha256': 'manifest'})
    monkeypatch.setattr(report.strategy_intelligence, 'trust_for', lambda *args: ({2021: 'VERIFIED'}, {}))
    assert report.verify_result('s', result, {'state': 'REUSE', 'run_id': 'r1'}, context)['run']['identity_status'] == 'IDENTITY_STALE'


def test_invalid_result_without_run_has_no_trusted_years(monkeypatch):
    from scripts import platform_v2_readiness as report
    context = {'evaluation': {'snapshot_id': 'snap', 'evaluation_id': 'eval'}, 'code': {}}
    monkeypatch.setattr(report.strategy_intelligence, 'pick_run', lambda *args: (None, 'DATA_BLOCKED'))
    monkeypatch.setattr(report.strategy_intelligence, 'trust_for', lambda *args: ({2021: 'VERIFIED'}, {}))
    result = {'strategy_id': 's', 'identity': 'bad', 'identity_inputs': {}, 'run': None,
              'year_status': {'2021': 'VERIFIED'}, 'status': 'ANALYZED'}
    actual = report.verify_result('s', result, {'state': 'DATA_BLOCKED'}, context)
    assert actual['status'] == 'IDENTITY_STALE' and actual['year_status'] == {}


def test_generated_report_rejects_future_dates_and_cannot_overwrite(tmp_path, monkeypatch):
    import json
    from scripts import platform_v2_readiness as report
    evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': '2025-12-31',
                'source_timestamp': '2026-01-01T00:00:00Z', 'data_trust': 'PARTIAL',
                'identity': 'SR1-id', 'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED'],
                'snapshot_id': 'snap', 'evaluation_id': 'eval'}
    row = {'year_status': {'2026': 'VERIFIED'}, 'backtest_window': ['2020-01-01', '2025-12-31']}
    monkeypatch.setattr(report, 'OUTPUT', tmp_path)
    monkeypatch.setattr(report, 'build_verified', lambda: (evidence, {'strategies': [row]}))
    with pytest.raises(ValueError, match='OD-2'):
        report.generate()
    assert not (tmp_path / 'STRATEGY_READINESS_21.json').exists()
    row['year_status'] = {'2025': 'VERIFIED'}
    report.generate()
    path = tmp_path / 'STRATEGY_READINESS_21.json'
    assert json.loads(path.read_text(encoding='utf-8'))['evidence'] == evidence
    with pytest.raises(FileExistsError):
        report.generate()


def test_replace_archives_the_previous_file(tmp_path, monkeypatch):
    import json
    from scripts import platform_v2_readiness as report
    evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': '2025-12-31', 'source_timestamp': '2026-01-01T00:00:00Z', 'data_trust': 'PARTIAL',
                'identity': 'SR1-old', 'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED'], 'snapshot_id': 'snap', 'evaluation_id': 'eval'}
    monkeypatch.setattr(report, 'OUTPUT', tmp_path)
    monkeypatch.setattr(report, 'build_verified', lambda: (evidence, {'strategies': []}))
    report.generate()
    original = (tmp_path / 'STRATEGY_READINESS_21.json').read_bytes()
    monkeypatch.setattr(report, 'build_verified', lambda: (evidence | {'identity': 'SR1-new'}, {'strategies': []}))
    report.generate(replace=True)
    assert json.loads((tmp_path / 'STRATEGY_READINESS_21.json').read_text(encoding='utf-8'))['evidence']['identity'] == 'SR1-new'
    assert (tmp_path / 'acceptance' / 'readiness_history' / 'STRATEGY_READINESS_21_SR1-old.json').read_bytes() == original


def test_future_snapshot_rejected_before_loading_context(monkeypatch):
    from types import SimpleNamespace
    from scripts import platform_v2_readiness as report
    monkeypatch.setattr(report.historical_foundation, 'latest_evaluation', lambda: {'snapshot_id': 'snap'})
    monkeypatch.setattr(report.historical_foundation, 'snapshot', lambda _: SimpleNamespace(manifest={'window': ['2015-01-01', '2026-01-01']}))
    monkeypatch.setattr(report.strategy_intelligence, 'load_context', lambda: pytest.fail('loaded future data'))
    with pytest.raises(ValueError, match='OD-2'):
        report.build_verified()


def test_future_run_rejected_before_loading_nav(monkeypatch):
    from core.backtest import run_store
    from scripts import platform_v2_readiness as report
    monkeypatch.setattr(report.strategy_intelligence, 'pick_run', lambda *args: ('r1', 'CURRENT'))
    monkeypatch.setattr(run_store, 'load_run', lambda *args, **kwargs: {'run': {'period': ['2020-01-01', '2026-01-01']}})
    monkeypatch.setattr(report.strategy_intelligence, 'load_run', lambda _: pytest.fail('loaded future run data'))
    with pytest.raises(ValueError, match='OD-2'):
        report.verify_result('s', {'identity_inputs': {}}, {'state': 'REUSE'}, {'evaluation': {'snapshot_id': 'snap'}})
