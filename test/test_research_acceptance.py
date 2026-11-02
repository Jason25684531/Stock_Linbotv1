import json
import sqlite3
from contextlib import contextmanager

import pytest

from scripts import research_acceptance as acceptance


def test_degradation_staleness_and_source_stop_are_reported():
    result = acceptance.monitoring({'rolling_metrics': [
        {'strategy': 'A', 'metric': 'sharpe', 'value': .2, 'minimum': .5},
        {'strategy': 'B', 'metric': 'sharpe', 'value': None, 'minimum': .5}],
        'daily_market': {'latest_official_session': '2025-01-02', 'as_of': '2025-01-08',
                         'calendar_complete_through': '2025-01-08',
                         'official_sessions': ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08']},
        'source_stops': [{'source': 'twse', 'reason': 'HTTP_429'}]})
    assert {row['flag'] for row in result['flags']} == {'DEGRADED', 'STALE', 'SOURCE_ANOMALY'}
    assert result['unknown'] == [{'strategy': 'B', 'metric': 'sharpe', 'reason': 'METRIC_UNAVAILABLE'}]
    assert any(row['reason'] == 'OFFICIAL_CALENDAR_OR_CACHE_DATE_UNAVAILABLE' for row in acceptance.monitoring({'daily_market': {}})['unknown'])
    with pytest.raises(ValueError, match='INVALID_REGISTERED_THRESHOLD'):
        acceptance.monitoring({'rolling_metrics': [{'strategy': 'A', 'metric': 'x', 'value': 1, 'minimum': float('inf')}]})


def test_synthetic_success_does_not_certify_real_goals(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, 'OUTPUT_ROOT', tmp_path)
    monkeypatch.setattr(acceptance, 'MONITOR', tmp_path / 'missing-monitor.json')
    monkeypatch.setattr(acceptance, 'SNAPSHOT', tmp_path / 'missing-snapshot')
    monkeypatch.setattr(acceptance, '_command', lambda name, args, output: {'status': 'PASS', 'evidence': name})
    monkeypatch.setattr(acceptance.research_verify_synthetic, 'verify', lambda path: {'tracks': [None] * 24})
    monkeypatch.setattr(acceptance.research_workflow, 'verify_trials', lambda: 'PASS')
    monkeypatch.setattr(acceptance.research_workflow, 'effective_trials', lambda: 743)
    monkeypatch.setattr(acceptance, 'budget_anomalies', lambda: {'status': 'PASS', 'flags': []})
    output = tmp_path / 'acceptance'
    result = acceptance.accept(output)
    assert result['overall'] == 'BLOCKED'
    assert all(row['status'] == 'BLOCKED' for row in result['goals'].values())
    assert json.loads((output / 'acceptance_report.json').read_text(encoding='utf-8'))['checks']['full_pytest']['status'] == 'PASS'
    with pytest.raises(FileExistsError):
        acceptance.accept(output)


def test_dashboard_goal_reflects_actual_available_snapshot(tmp_path, monkeypatch):
    from app import dashboard_payloads
    monkeypatch.setattr(dashboard_payloads, 'build_research_status_payload',
                        lambda _: {'status': 'OK', 'research_only': True, 'strategies': [{}] * 22})
    goals = acceptance.assess_goals({'integration': {'status': 'PASS'}}, {'unknown': [{'reason': 'missing'}]},
                                    {'status': 'UNKNOWN'}, tmp_path)
    assert goals['GOAL_4']['status'] == 'PASS'
    assert goals['GOAL_1']['status'] == goals['GOAL_2']['status'] == 'BLOCKED'


def test_source_stop_and_schema_drift_are_read_from_budget_ledger(monkeypatch):
    from core.runtime import research_budget
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE cooldowns (rate_group TEXT, until_at REAL, reason TEXT);'
                       'CREATE TABLE attempts (context TEXT, status INTEGER, error TEXT);')
    conn.execute('INSERT INTO cooldowns VALUES (?, ?, ?)', ('twse', 1, 'source stop'))
    conn.execute('INSERT INTO attempts VALUES (?, ?, ?)', (json.dumps({'source': 'mops'}), 200, 'schema drift'))
    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(research_budget, 'connection', connection)
    result = acceptance.budget_anomalies()
    assert result['status'] == 'PASS'
    assert {(row['source'], row['reason']) for row in result['flags']} == {('twse', 'source stop'), ('mops', 'SCHEMA_DRIFT')}
    conn.close()


def test_not_evaluable_degradation_is_a_state_that_does_not_block_monitoring_acceptance(tmp_path, monkeypatch):
    checks = {name: {'status': 'PASS'} for name in (*acceptance.CATEGORIES, 'full_pytest', 'openspec_strict', 'git_diff_check',
                                                    'synthetic_manifest_and_reconciliation', 'trial_ledger')}
    state = {'flags': [], 'unknown': [], 'states': [{'scope': 'degradation', 'status': 'NOT_EVALUABLE', 'reason': 'NO_VERIFIED_STRATEGY'}]}
    assert acceptance.assess_goals(checks, state, {'status': 'PASS'}, tmp_path)['GOAL_5']['status'] == 'PASS'
    missing = {**state, 'unknown': [{'scope': 'degradation', 'reason': 'STATUS_SNAPSHOT_MISSING'}]}
    assert acceptance.assess_goals(checks, missing, {'status': 'PASS'}, tmp_path)['GOAL_5']['status'] == 'BLOCKED'
