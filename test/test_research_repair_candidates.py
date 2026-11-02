import csv
import json

import pytest

from core.research import repair_candidates as candidate, repair_diagnostics as diagnostics
from scripts import research_workflow as workflow


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.csv'
    ledger = tmp_path / 'trials.jsonl'
    monkeypatch.setattr(workflow, 'REGISTRY', registry)
    monkeypatch.setattr(workflow, 'TRIAL_LEDGER', ledger)
    return registry, ledger


def observations(path):
    records = []
    for stock, value in [('AAA', 2), ('BBB', 1)]:
        for factor in candidate.FACTORS:
            records.append({'stock_id': stock, 'factor': factor, 'value': value, 'period_end': '2025-06-30',
                            'published_at': '2025-09-01T10:00:00+08:00', 'available_at': '2025-09-02T09:00:00+08:00',
                            'source': 'research_fixture', 'source_sha256': 'a' * 64})
    records.append({**records[4], 'value': 999, 'published_at': '2025-09-06T10:00:00+08:00', 'available_at': '2025-09-06T11:00:00+08:00'})
    path.write_text(json.dumps(records), encoding='utf-8')
    return path


def test_registration_required_before_scoring_or_trial(isolated_registry, tmp_path):
    registry, ledger = isolated_registry
    workflow.register({**candidate.registration(), 'hypothesis_id': 'OTHER'}, path=registry)
    with pytest.raises(ValueError, match='HYPOTHESIS_NOT_REGISTERED'):
        candidate.candidates(observations(tmp_path / 'input.json'), '2025-09-05T15:00:00+08:00')
    assert not ledger.exists()


def test_official_registration_command_uses_the_declared_hypothesis(monkeypatch):
    captured = []
    monkeypatch.setattr(workflow, 'register', lambda row: captured.append(row) or row)
    assert workflow.cmd_register_repair_candidates() == candidate.registration()
    assert captured == [candidate.registration()]


def test_candidate_registration_rejects_production(monkeypatch):
    monkeypatch.setattr('core.runtime.operation_policy.current_mode', lambda: candidate.operation_policy.ExecutionMode.PRODUCTION)
    with pytest.raises(PermissionError, match='RESEARCH_MODE_REQUIRED'):
        workflow.cmd_register_repair_candidates()
    with pytest.raises(PermissionError, match='RESEARCH_MODE_REQUIRED'):
        candidate.candidates('not-read.json', '2025-09-05T15:00:00+08:00')


def test_pit_excludes_late_publication_and_records_trial_then_replay(isolated_registry, tmp_path):
    registry, ledger = isolated_registry
    workflow.register(candidate.registration(), path=registry)
    path = observations(tmp_path / 'input.json')
    result = candidate.candidates(path, '2025-09-05T15:00:00+08:00')
    assert result['label'] == 'RESEARCH_ONLY' and not result['formal_recommendation']
    assert result['data_status'] == 'BLOCKED_UNVERIFIED_SOURCES'
    assert result['eligible_observations'] == 8 and [r['stock_id'] for r in result['candidates']] == ['AAA', 'BBB']
    assert result['trial_kind'] == 'TRIAL'
    again = candidate.candidates(path, '2025-09-05T15:00:00+08:00')
    assert again['trial_kind'] == 'REPLAY' and again['experiment_id'] == result['experiment_id']
    assert workflow.verify_trials(ledger) == 'PASS' and workflow.effective_trials(ledger) == 744


def test_oos_timezone_boundary_and_missing_timestamps_fail_closed(isolated_registry, tmp_path):
    registry, ledger = isolated_registry
    workflow.register(candidate.registration(), path=registry)
    path = observations(tmp_path / 'input.json')
    with pytest.raises(ValueError, match='OOS_BLOCKED'):
        candidate.candidates(path, '2026-01-01T00:00:00+08:00')
    with pytest.raises(ValueError, match='OOS_BLOCKED'):
        candidate.candidates(path, '2025-12-31T23:59:59-05:00')
    with pytest.raises(ValueError, match='PIT_TIMESTAMP_REQUIRES_TIMEZONE'):
        candidate.candidates(path, '2025-09-05T15:00:00')
    records = json.loads(path.read_text())
    records[0]['available_at'] = '2025-08-01T10:00:00+08:00'
    path.write_text(json.dumps(records))
    with pytest.raises(ValueError, match='INVALID_PIT_CHRONOLOGY'):
        candidate.candidates(path, '2025-09-05T15:00:00+08:00')
    assert not ledger.exists()


def test_missing_features_or_ambiguous_records_never_spend_a_trial(isolated_registry, tmp_path):
    registry, ledger = isolated_registry
    workflow.register(candidate.registration(), path=registry)
    path = observations(tmp_path / 'input.json')
    rows = json.loads(path.read_text())
    path.write_text(json.dumps([row for row in rows if row['factor'] != 'quality']))
    assert candidate.candidates(path, '2025-09-05T15:00:00+08:00')['data_status'] == 'BLOCKED_MISSING_PIT_FEATURES'
    assert not ledger.exists()
    rows.append({**rows[0], 'value': 8})
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='AMBIGUOUS_PIT_OBSERVATION'):
        candidate.candidates(path, '2025-09-05T15:00:00+08:00')
    assert not ledger.exists()


def test_read_only_diagnostics_preserves_unrelated_strategy_status(tmp_path, monkeypatch):
    matrix = tmp_path / 'matrix.csv'
    fields = ['group', 'strategy', 'DATA_STATUS', 'SIGNAL_STATUS', 'BACKTEST_STATUS', 'STATISTICAL_STATUS', 'FINAL_QUALIFICATION', 'reasons']
    with matrix.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(dict(zip(fields, ['B3_DAY3', 'A191_132', 'DATA_BLOCKED', 'KEEP', 'DIAGNOSTIC_ONLY', 'FAIL', 'NOT_QUALIFIED', 'missing actions'])))
        writer.writerow(dict(zip(fields, ['A_NATIVE_AI', 'OTHER', 'UNKNOWN', 'UNKNOWN', 'NOT_RUN', 'NOT_EVALUATED', 'NOT_QUALIFIED', 'not evaluated'])))
    summary = tmp_path / 'summary.json'
    summary.write_text(json.dumps({'label': 'DIAGNOSTIC_ONLY', 'outcome': 'REJECTED'}))
    monkeypatch.setattr(diagnostics, 'MATRIX', matrix)
    monkeypatch.setattr(diagnostics, 'A191_SUMMARY', summary)
    before = matrix.read_bytes(), summary.read_bytes()
    result = diagnostics.diagnostics()
    assert result['strategies'][0]['repair_decision'] == 'BLOCKED'
    assert result['strategies'][0]['pit_status'] == 'BLOCKED_LEGACY_NEXT_SESSION_TRADABILITY'
    assert 'diagnostic_outcome' not in result['strategies'][1]
    assert result['strategies'][1]['STATISTICAL_STATUS'] == 'NOT_EVALUATED'
    assert result['strategies'][1]['repair_decision'] == 'BLOCKED'
    assert before == (matrix.read_bytes(), summary.read_bytes())
    matrix.write_text(matrix.read_text().replace('SIGNAL_STATUS', 'MISSING_SIGNAL'), encoding='utf-8')
    assert diagnostics.diagnostics()['status'] == 'BLOCKED'


def test_strategy_elimination_requires_that_strategys_credible_complete_evidence():
    row = {'DATA_STATUS': 'DATA_CERTIFIED', 'SIGNAL_STATUS': 'KEEP', 'BACKTEST_STATUS': 'CREDIBLE', 'STATISTICAL_STATUS': 'FAIL'}
    assert diagnostics.assess(row) == 'REJECTED'
    assert diagnostics.assess({**row, 'STATISTICAL_STATUS': 'PASS'}) == 'ELIGIBLE_FOR_OOS_REVIEW'
    assert diagnostics.assess({**row, 'STATISTICAL_STATUS': 'PASS', 'pit_status': 'BLOCKED_LEGACY_NEXT_SESSION_TRADABILITY'}) == 'BLOCKED'
    for key in row:
        assert diagnostics.assess({**row, key: 'UNKNOWN'}) == 'BLOCKED'


def test_diagnostic_cli_reports_blocked_without_writing(tmp_path, monkeypatch, capsys):
    from scripts import research_repair_diagnostics as cli
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    monkeypatch.setattr(cli, 'diagnostics', lambda: {'status': 'BLOCKED', 'strategies': [], 'reason': 'missing evidence'})
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'BLOCKED'
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
