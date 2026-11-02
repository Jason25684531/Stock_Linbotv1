import csv
import hashlib
import json

from scripts import research_a191_blockers as report


def test_twelve_configs_are_blocked_without_fabricating_results(tmp_path, monkeypatch):
    manifest = tmp_path / 'manifest.csv'
    with manifest.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['strategy_id', 'factor_set'])
        writer.writeheader()
        writer.writerows({'strategy_id': f'c{i}', 'factor_set': 'A191_132'} for i in range(12))
    coverage = tmp_path / 'coverage.json'
    coverage.write_text(json.dumps({'observed_events': 444, 'verified_coverage_ratio': None}), encoding='utf-8')
    monkeypatch.setattr(report, 'MANIFEST', manifest)
    monkeypatch.setattr(report, 'PINNED', hashlib.sha256(manifest.read_bytes()).hexdigest())
    monkeypatch.setattr(report, 'COVERAGE', coverage)
    monkeypatch.setattr(report, 'OUTPUT_ROOT', tmp_path)
    stocks, evidence = tmp_path / 'stocks.csv', tmp_path / 'evidence.jsonl'  # never the real evidence, stock list or budget audit
    stocks.write_text('stock_id\nS1\nS2\n', encoding='utf-8')
    from core.research.evidence_ledger import append
    append(evidence, [dict(stock_id='S1', event_date='2025-07-01', field='ex_date', value_or_UNKNOWN='2025-07-01', source='fixture',
                           sha256='a' * 64, fetched_at=None, grant_scope='fixture', tier='LOCAL_CACHE_UNCERTIFIED')])
    monkeypatch.setattr(report, 'STOCKS', stocks)
    monkeypatch.setattr(report, 'EVIDENCE', evidence)
    monkeypatch.setattr(report, 'verified_sources', lambda: {})
    monkeypatch.setattr(report.workflow, 'verify_registry', lambda: 'PASS')
    monkeypatch.setattr(report.workflow, 'verify_trials', lambda: 'PASS')
    monkeypatch.setattr(report.workflow, '_trial_rows', lambda: [])
    monkeypatch.setattr(report.workflow, 'effective_trials', lambda: 743)
    result = report.report(tmp_path / 'report')
    assert len(result['configurations']) == 12 and result['registered_new_trials'] == 0
    assert result['event_denominator'] is None and result['observed_events_are_not_held_through_proof']
    assert all(row['formal_total_return']['value'] is None for row in result['configurations'])
    # The fixture ledger has no official rows, so the dry run blocks and nothing is admitted.
    assert result['dry_run_status'] == 'BLOCKED' and result['admission'] == 'NOT_ADMITTED'
    assert result['dry_run']['stocks_event_list_unverified'] and result['effective_trials'] == 743
    assert result['label'] == 'DIAGNOSTIC_ONLY' and result['dry_run']['total'] == 1
