"""Regression tests for independent review round 2 (2026-11-11). Synthetic data and tmp_path only."""
import json

import pytest

from core.research.intelligence import evaluate, jobstate, profile, scenarios
from scripts import strategy_intelligence as si


@pytest.fixture(autouse=True)
def no_database_in_the_shell(monkeypatch):
    """The shared page header reads MySQL for its status badge; these tests never need a database (patch the module, never app.services)."""
    from app import web_server
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda *a, **k: ({'status': 'ok'}, 200))
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda *a, **k: None)


def test_a_load_error_in_one_strategy_is_isolated_and_counted_across_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(si, 'OUT', tmp_path)
    monkeypatch.setattr(si, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(si, 'catalog_ids', lambda: ['good', 'bad'])
    monkeypatch.setattr(si, 'load_context', lambda: {'profile_id': 'EP1-x', 'evaluation': {'snapshot_id': 'HS1-x'}, 'code': {'a.py': '1'}})
    monkeypatch.setattr(si, '_portfolio', lambda ctx, job, results: None)
    monkeypatch.setattr(si, 'code_hashes', lambda: {'a.py': '1'})

    def analyze(ctx, job, sid):
        if sid == 'bad':
            raise FileNotFoundError('nav.csv')
        job.start(sid, 'performance', 'k', {})
        job.done(sid, 'performance', 'x', reused=False)
        result = {'identity': 'SI1-g', 'strategy_id': sid, 'identity_inputs': {'code': si.code_hashes()}}
        si.write_json(si.RESULTS / 'SI1-g' / f'{sid}.json', result)
        return result
    monkeypatch.setattr(si, '_analyze', analyze)
    assert si.run_job('j1') == 2
    row = json.loads((tmp_path / 'jobs' / 'j1.json').read_text())['rows']['bad|load']
    assert row['status'] == 'FAILED' and row['failure_class'] == 'DATA_MISSING'
    si.run_job('j2')
    si.run_job('j3')
    assert json.loads((tmp_path / 'jobs' / 'j3.json').read_text())['rows']['bad|load']['status'] == 'FAILED_WITH_EVIDENCE'
    si.run_job('j4')
    row = json.loads((tmp_path / 'jobs' / 'j4.json').read_text())['rows']['bad|load']
    assert row['reason'] == 'MAX_ATTEMPTS_REACHED' and row['retries'] == 3
    assert json.loads((tmp_path / 'LATEST.json').read_text())['failed'] == {'bad': 'FAILED_WITH_EVIDENCE'}


def test_memory_error_releases_memory_and_retries_once(tmp_path, monkeypatch):
    monkeypatch.setattr(si, 'ROOT', tmp_path)
    job = jobstate.Job(tmp_path, 'm')
    calls = []

    def compute():
        calls.append(1)
        if len(calls) == 1:
            raise MemoryError()
        return {'ok': True}
    assert si._stage(job, 'a', 'performance', tmp_path / 'results' / 'SI1-m', {}, compute) == {'ok': True}
    assert len(calls) == 2 and job.state['rows']['a|performance']['memory_retry'] is True


def test_the_engine_enforced_participation_cap_is_never_judged():
    prof = json.loads(si.PROFILE.read_text(encoding='utf-8'))
    ctx = {'verified': {'cagr': {'value': 0.1}}, 'provisional': {'cagr': {'value': 0.1}}, 'primary_type': 'trend_momentum',
           'liquidity': {'entry_participation_prev': {'p90': 0.2}}}
    item = next(i for i in evaluate.health(ctx, prof)['tradability'] if i['name'] == '進場參與率 P90')
    assert item['status'] == 'NOT_APPLICABLE' and item['threshold'].startswith('ENGINE_ENFORCED')


@pytest.mark.parametrize('check', [
    {'metric': 'payoff_ratio', 'op': '>', 'value': 1, 'source': 'DEFINITIONAL'},
    {'metric': 'forward_return_5', 'op': '>', 'value': 0, 'source': 'DEFINITIONAL'},
    {'metric': 'participation_p90', 'op': '<=', 'value': 0.05, 'source': 'ENGINE_CONSTANT'},
    {'metric': 'sharpe', 'op': '>', 'value': 'benchmark', 'source': 'BENCHMARK_SAME_PERIOD'}])
def test_thresholds_outside_the_allowed_sources_are_rejected(check):
    data = json.loads(si.PROFILE.read_text(encoding='utf-8'))
    profile.validate(data)
    data['types']['trend_momentum']['edge_checks'].append(check)
    with pytest.raises(ValueError, match='THRESHOLD_SOURCE'):
        profile.validate(data)


def test_the_scenario_sample_rule_has_one_value():
    data = json.loads(si.PROFILE.read_text(encoding='utf-8'))
    assert data['sample_rules']['scenarios'] == scenarios.RULE


def test_latest_refuses_results_from_older_code(tmp_path, monkeypatch):
    monkeypatch.setattr(si, 'RESULTS', tmp_path)
    si.write_json(tmp_path / 'SI1-a' / 's.json', {'identity': 'SI1-a', 'strategy_id': 's', 'identity_inputs': {'code': {'old.py': '0'}}})
    assert si.latest_problems({'strategies': {'s': 'SI1-a'}}) == ['s:SI1-a:CODE_CHANGED_SINCE_RESULT']


def test_a_strategy_without_a_stored_run_renders(tmp_path, monkeypatch):
    from app import app, intelligence_routes as ir
    folder = tmp_path / 'results' / 'SI1-0123456789abcdef'
    result = {'strategy_id': 'x', 'display_name': 'x', 'identity': 'SI1-0123456789abcdef', 'status': 'NO_STORED_RUN', 'reason': None, 'run': None,
              'certification': 'NOT_CERTIFIED', 'definition': {'definition_id': 'SD1-x', 'fields': {}, 'numeric': {}}, 'year_status': {},
              'profile': {'profile_id': 'EP1-x', 'secondary_types': [], 'type_evidence': '', 'targets': {'cagr_basic': 0.05, 'cagr_priority': 0.1},
                          'risk_budget': {'status': 'RESEARCH_THRESHOLD_UNSET', 'source': ''},
                          'type': {'label': 't', 'questions': [], 'primary_metrics': [], 'auxiliary_metrics': [], 'main_risks': [], 'suitable_regimes': [], 'unsuitable_regimes': []}}}
    si.write_json(folder / 'x.json', result)
    si.write_json(tmp_path / 'LATEST.json', {'strategies': {'x': 'SI1-0123456789abcdef'}})
    monkeypatch.setattr(ir, 'OUT', tmp_path)
    monkeypatch.setattr(ir, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(ir, '_catalog', lambda: ['x'])
    page = app.test_client().get('/intelligence/x')
    assert page.status_code == 200 and 'blocked-reason' in page.get_data(as_text=True)
