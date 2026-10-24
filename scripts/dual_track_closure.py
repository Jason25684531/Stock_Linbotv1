"""Closure checks for 2026-10-23-validate-22-strategies-dual-track: verification_closure.json and reproduction_manifest_closure.json.

Nothing here replaces the original verification.json (it stays FAIL) or the original manifest; the closure files sit next to them in closure/ and
state which exceptions are still open for the Owner.
"""
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dual_track_validation as dtv
from scripts.audit_strategy_history import digest, read, write
from scripts.official_data_recovery import ARCHIVES, RUNTIME_STATUS, archived

OUT, CLOSURE = dtv.OUT, dtv.OUT / 'closure'
OPERATION = ROOT / 'outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1'
ALLOWED_CODE = dtv.EXPECTED_CODE_CHANGES | {'core/research/sources/twse.py'}  # RequestGate: optional cross-process state, measured gap
TARGETED_TESTS = ['test/test_request_gate.py', 'test/unit/research/test_twse_adapter.py', 'test/test_production_status_isolation.py', 'test/test_run_daily_persistence.py',
                  'test/test_valuation_policy.py', 'test/test_native7_rules_only.py', 'test/test_dual_track_validation.py', 'test/test_official_data_recovery.py',
                  'test/test_stabilize_daily_recommendation_pipeline_contract.py', 'test/unit/runtime']
CLOSURE_FILES = ('mops_sample_200.csv', 'mops_sample_summary.json', 'mops_population_basis_scan.json', 'test_isolation_record.json', 'request_spacing_exception_report.md',
                 'openspec_count_reconciliation.json', 'strategy_status_matrix_closure.csv', 'strategy_status_summary_closure.json', 'full_suite_run2.log', 'closure_report.md')


def new(name):
    path = CLOSURE / name
    if path.exists():
        raise FileExistsError(path)
    return path


def verify():
    from scripts.validate_native22 import preservation

    original = read(OUT / 'verification.json')
    assert original['status'] == 'FAIL' and original['failed_checks'] == ['REQUEST_SPACING'], 'ORIGINAL_VERIFICATION_NOT_PRESERVED'
    start = read(OUT / 'baseline.json')
    assert start['model_sha256'] == {k: digest(ROOT / p) for k, (_n, p) in dtv.models().items()}, 'MODEL_CHANGED'
    assert all(not v['changed'] for v in archived(ARCHIVES + [dtv.PREV_OUT]).values()), 'ARCHIVED_OUTPUT_CHANGED'
    kept = preservation()
    assert set(kept['protected_changed']) <= dtv.KNOWN_EXCEPTIONS and set(kept['historical_changed']) <= {RUNTIME_STATUS}, 'HISTORICAL_OR_PROTECTED_CHANGE'
    changed = dtv.git('diff', '--name-only', start['git_head'], '--', 'jobs', 'core', 'config', 'execution', 'app', 'services', 'strategy_settings.json', 'ML_Data').split()
    assert set(changed) <= ALLOWED_CODE, f'UNEXPECTED_CODE_CHANGE {changed}'
    assert digest(dtv.FRESH_LEDGER) == start['fresh_oos_ledger_sha256'], 'FRESH_OOS_LEDGER_CHANGED'
    od29 = read(OUT / 'od29_valuation' / 'replay_comparison.json')
    assert od29['runner_sha256']['after'] == digest(ROOT / 'core/backtest/runner.py'), 'RUNNER_CHANGED_SINCE_CHARACTERIZATION'
    isolation = read(CLOSURE / 'test_isolation_record.json')
    status_now = OPERATION / 'fundamental_production_status.json'
    now = {p.relative_to(OPERATION).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(OPERATION.rglob('*')) if p.is_file()}
    after = {k: [v['size'], v['mtime_ns']] for k, v in isolation['after']['files'].items()}
    assert now == after, 'PRODUCTION_FOLDER_CHANGED_SINCE_ISOLATION_RECORD'
    status_before = digest(status_now), status_now.stat().st_mtime_ns
    run = subprocess.run([sys.executable, '-m', 'pytest', *TARGETED_TESTS, '-q', '-p', 'no:cacheprovider'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    status_after_tests = digest(status_now), status_now.stat().st_mtime_ns
    assert status_after_tests == status_before, 'STATUS_FILE_CHANGED_BY_TARGETED_TESTS'
    mops, spacing = read(CLOSURE / 'mops_sample_summary.json'), (CLOSURE / 'request_spacing_exception_report.md').read_text(encoding='utf-8')
    openspec = read(CLOSURE / 'openspec_count_reconciliation.json')
    open_items = ['OWNER_DECISION_REQUEST_SPACING_EXCEPTION (options A/B/C; original verification.json stays FAIL)',
                  'MOPS_POPULATION_NOT_CERTIFIED (sample 200 only; 468 operating_income_yoy rows mix standalone/cumulative bases; dataset fix needs Owner approval)',
                  'PRODUCTION_STATUS_FILE_CONTENT_IS_TEST_DATA (asof 2026-04-28; not restored without Owner approval; the 18:30 +08 evening pipeline rewrites it)',
                  'TWO_PREEXISTING_TEST_FAILURES (BacktestEngine without research_data; core.research imports core.calc_indicators) need Owner decision on core edits']
    write(new('verification_closure.json'), {
        'status': 'CLOSURE_CHECKS_PASS_WITH_OPEN_EXCEPTIONS', 'at': datetime.now().isoformat(timespec='seconds'),
        'original_verification': {'path': 'verification.json', 'sha256': digest(OUT / 'verification.json'), 'status': original['status'], 'failed_checks': original['failed_checks'], 'preserved': True},
        'models_unchanged': True, 'archived_outputs_unchanged': True, 'protected_source_exceptions': sorted(kept['protected_changed']), 'historical_hash_exceptions': sorted(kept['historical_changed']),
        'code_changed_since_baseline': sorted(changed), 'allowed_code_changes': sorted(ALLOWED_CODE), 'fresh_oos_ledger_unchanged': True, 'runner_unchanged_since_od29_characterization': True,
        'production_folder': {'path': str(OPERATION), 'unchanged_since_full_suite_isolation_record': True, 'status_file_sha256': status_after_tests[0],
                              'status_file_content': 'byte-identical to the output of test_run_daily_persistence redirected to a temp folder (test data, asof 2026-04-28)'},
        'targeted_tests': {'files': TARGETED_TESTS, 'returncode': run.returncode, 'summary': run.stdout.strip().splitlines()[-1]},
        'mops_sample': {'size': mops['sample_size'], 'status_counts': mops['status_counts'], 'seed': mops['seed']},
        'openspec_counts': {'entries': openspec['entries_in_openspec_specs'], 'spec_folders': openspec['spec_folders'], 'validated': openspec['validated_by_strict'], 'plain_files': openspec['plain_files_not_specs']},
        'open_exceptions_for_owner': open_items, 'ARCHIVE_ELIGIBLE': False,
        'no_new_official_requests': True, 'no_model_trained': True, 'mysql': 'no write path', 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('CLOSURE VERIFY', run.stdout.strip().splitlines()[-1], '| open exceptions:', len(open_items))


def manifest():
    write(new('reproduction_manifest_closure.json'), {
        'change': 'openspec/changes/2026-10-23-validate-22-strategies-dual-track', 'git_head': dtv.git('rev-parse', 'HEAD').strip(), 'original_manifest_sha256': digest(OUT / 'reproduction_manifest.json'),
        'commands': ['python scripts/test_isolation_record.py   # full suite with before/after state of the production folder', 'python scripts/mops_sample_check.py sample', 'python scripts/mops_sample_check.py scan',
                     'python scripts/dual_track_matrix.py closure', 'python scripts/dual_track_closure.py verify', 'python scripts/dual_track_closure.py manifest'],
        'seed': {'mops_sample': 20261024}, 'code': {p: digest(ROOT / p) for p in ('core/research/sources/twse.py', 'scripts/dual_track_validation.py', 'scripts/mops_sample_check.py', 'scripts/test_isolation_record.py',
                                                                                'scripts/dual_track_matrix.py', 'scripts/dual_track_closure.py', 'test/conftest.py', 'test/test_request_gate.py',
                                                                                'test/test_production_status_isolation.py')},
        'inputs': {'fundamental_records.parquet': digest(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet'),
                   'publication_records_full.csv': digest(ROOT / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/publication_records_full.csv')},
        'outputs': {name: digest(CLOSURE / name) for name in CLOSURE_FILES + ('verification_closure.json',)}, 'environment': {'python': platform.python_version()}, 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('closure manifest written')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'verify': verify, 'manifest': manifest}[sys.argv[1]]()
