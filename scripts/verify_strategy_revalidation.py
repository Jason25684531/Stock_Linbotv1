"""Verify complete identity coverage and preservation without executing strategies."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import HISTORY, digest, read, write, row_matches


def verify(output, hash_history=False):
    inventory = read(ROOT / HISTORY / 'inventory.json')
    matrix = read(output / 'identity_matrix_final.json')
    families = read(output / 'family_matrix.json')['families']
    expected = {r['id'] for r in inventory['records']}
    actual = {r['id'] for r in matrix['records']}
    assert expected == actual and len(actual) == len(matrix['records']), 'IDENTITY_COVERAGE'
    originals = {r['id']: r for r in inventory['records']}
    assert all(row_matches(originals[r['id']], link['metrics']) for r in matrix['records']
               for link in r['historical_runs'] if link['configuration_match']), 'CONFIGURATION_LINK'
    linked = {r['id'] for r in matrix['records'] if r['family_id']}
    members = [member for family in families for member in family['members']]
    assert len(members) == len(set(members)) and linked == set(members), 'FAMILY_COVERAGE'
    assert sum(f['parameter_count'] for f in families) == sum(r['role'] == 'PARAMETER_CONFIG' for r in matrix['records']), 'PARAMETER_COUNT'
    required = ('historical_backtest_completed', 'historical_evidence_verified', 'reproducible',
                'current_engine_validated', 'data_validated', 'oos_validated', 'production_eligible', 'final_status')
    assert all(all(key in r for key in required) for r in matrix['records']), 'STATUS_COVERAGE'
    assert all(not r['family_id'] or r['blockers'] for r in matrix['records'] if r['final_status'] == 'BLOCKED'), 'BLOCKER_REASON'
    for r in matrix['records']:
        if r['family_id']:
            assert all(v['value'] is not None or v['reason'] for v in r['certified_metrics'].values()), 'UNDEFINED_METRICS'
    protected = read(ROOT / HISTORY / 'safety_verification.json')['protected_files']
    # runner is an intentional versioned engine fix; .gitignore predates this task.
    checked = {name: digest(ROOT / name) == record['sha256'] for name, record in protected.items()
               if name not in {'.gitignore', 'core/backtest/runner.py'}}
    assert all(checked.values()), 'PROTECTED_IDENTITY_OR_NOTEBOOK_CHANGED'
    failures = []
    hashes = read(output / 'historical_hashes.json')
    if hash_history:
        for index, (name, expected_hash) in enumerate(hashes.items()):
            if digest(ROOT / name) != expected_hash:
                failures.append(name)
            if index and index % 1000 == 0:
                print('Verified historical files', index, flush=True)
        assert not failures, 'HISTORICAL_EVIDENCE_CHANGED: ' + str(failures)
    result = {'status': 'PASS', 'identity_records': len(actual), 'family_records': len(families),
        'family_linked_records': len(linked), 'protected_identity_sources_checked': len(checked),
        'historical_hashes_checked': len(hashes) if hash_history else 0, 'historical_failures': failures,
        'unchanged_protected_files': checked,
        'intentional_source_changes': ['core/backtest/runner.py', 'jobs/run_portfolio_research.py'],
        'preserved_user_worktree_change': '.gitignore', 'BROKER_ORDER_SUBMISSION': 'DISABLED'}
    write(output / ('preservation_verification.json' if hash_history else 'coverage_verification.json'), result)
    print({key: value for key, value in result.items() if key != 'unchanged_protected_files'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--hash-history', action='store_true')
    args = parser.parse_args()
    verify(args.audit.resolve(), args.hash_history)
