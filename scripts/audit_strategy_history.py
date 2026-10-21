"""Read-only historical evidence audit; no strategy execution or ledger mutation."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = Path('outputs/strategy_validation/2026-10-21')
FAMILIES = Path('outputs/strategy_family_inventory/2026-10-21-final/strategy_families.json')
NAMESPACES = {
    'strategy_research': ('strategy_backtest_scoreboard.csv', 'strategy_id'),
    'portfolio_research': ('portfolio_scoreboard.csv', 'config_id'),
    'fundamental_strategy_validation': ('strategy_scoreboard.csv', 'strategy_id'),
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def hash_claims(value, prefix=''):
    """Only explicit file-to-hash maps are claims; arbitrary status strings aren't."""
    if isinstance(value, dict):
        for key, item in value.items():
            name = prefix + '/' + key
            if isinstance(item, str) and len(item) == 64 and all(c in '0123456789abcdef' for c in item.lower()):
                if Path(key).suffix in {'.csv', '.json', '.parquet', '.md', '.yaml', '.py'}:
                    yield name, key, item
            elif isinstance(item, dict):
                yield from hash_claims(item, name)


def verify_manifest(path, root, by_name, hashes):
    checks = []
    for field, key, expected in hash_claims(read(path)):
        local = path.parent / key
        candidates = [local] if local.is_file() else by_name.get(Path(key).name, [])
        matching = [p for p in candidates if hashes.get(p.relative_to(root).as_posix()) == expected]
        checks.append({'field': field, 'expected': expected,
                       'status': 'PASS' if matching else 'FAIL' if local.is_file() else 'BLOCKED',
                       'paths': [p.relative_to(root).as_posix() for p in matching],
                       'reason': None if matching else 'HASH_MISMATCH' if local.is_file() else 'REFERENCED_FILE_NOT_RESOLVED'})
    return {'path': path.relative_to(root).as_posix(), 'sha256': hashes[path.relative_to(root).as_posix()],
            'status': ('PASS' if all(c['status'] == 'PASS' for c in checks) else 'FAIL' if any(c['status'] == 'FAIL' for c in checks) else 'BLOCKED') if checks else 'NOT_VERIFIABLE',
            'checks': checks}


def row_matches(record, row):
    """ID equality alone is insufficient when configuration hashes are available."""
    expected = record.get('configuration_fingerprint')
    actual = row.get('config_hash')
    if expected and actual:
        return expected == actual
    aliases = {'stock_weighting': 'portfolio_weighting'}
    parameters = record.get('parameters') or {}
    for key in ('top_n', 'rebalance_days', 'factor_set', 'factor_weighting', 'portfolio_weighting', 'combination_method', 'stock_weighting'):
        target = aliases.get(key, key)
        if key in row and target in parameters:
            if str(row[key]).lower() != str(parameters[target]).lower():
                return False
    return True


def audit(root, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError('OUTPUT_EXISTS: use a new immutable audit directory')
    output.mkdir(parents=True, exist_ok=True)
    family = read(root / FAMILIES)
    inventory = read(root / HISTORY / 'inventory.json')
    validation = {r['id']: r for r in read(root / HISTORY / 'validation.json')['records']}
    originals = {r['id']: r for r in inventory['records']}
    assert set(originals) == {r['id'] for r in family['records']} == set(validation)
    paths, excluded = [], []
    for base in ('outputs', 'artifacts', 'Strategy_example', 'data/processed'):
        for directory, dirs, files in os.walk(root / base):
            for name in list(dirs):
                p = Path(directory) / name
                if name.endswith('cache') or name in {'__pycache__', 'baseline_sources'} or p == output:
                    excluded.append(p.relative_to(root).as_posix())
                    dirs.remove(name)
            paths.extend(Path(directory) / name for name in files if Path(name).suffix in {'.json', '.csv', '.parquet', '.ipynb', '.md', '.log'})
    paths.sort()
    write(output / 'scan_scope.json', {'roots': ['outputs', 'artifacts', 'Strategy_example', 'data/processed'],
        'excluded_raw_cache_or_archived_source_subtrees': excluded, 'artifact_count': len(paths),
        'scope': 'Produced evidence and processed inputs; raw provider cache payloads are not individually rehashed or executed.'})
    print('Hashing', len(paths), 'evidence files', flush=True)
    hashes, by_name = {}, defaultdict(list)
    for path in paths:
        relative = path.relative_to(root).as_posix()
        hashes[relative] = digest(path)
        by_name[path.name].append(path)
    for name in ('strategy_full_inventory.md', 'strategy_validation_report.md', 'strategy_family_inventory.md'):
        source = root / 'docs' / name
        (output / ('before_' + name)).write_bytes(source.read_bytes())
    write(output / 'historical_hashes.json', hashes)
    manifest_paths = [p for p in paths if p.suffix == '.json' and ('manifest' in p.name.lower() or p.name == 'reproducibility.json')]
    manifests, errors = {}, []
    for path in manifest_paths:
        try:
            m = verify_manifest(path, root, by_name, hashes)
            manifests[m['path']] = m
        except (ValueError, OSError) as exc:
            errors.append({'path': path.relative_to(root).as_posix(), 'error': type(exc).__name__})
    write(output / 'manifest_verification.json', {'manifests': list(manifests.values()), 'errors': errors})
    tables = defaultdict(list)
    for namespace, (filename, column) in NAMESPACES.items():
        for path in by_name[filename]:
            # Explicit source namespace, including prior immutable replay directories.
            if namespace not in path.parts and not any(source['path'].startswith(path.parent.relative_to(root).as_posix() + '/')
                for r in originals.values() if r.get('namespace') == namespace for source in r['sources']):
                continue
            for line, row in enumerate(csv_rows(path), 2):
                tables[(namespace, row[column])].append((path, line, row))
    stocks = read(root / HISTORY / 'stocks.json')
    replay = read(root / HISTORY / 'replay_reproducibility.json')
    replay_checks = {}
    for namespace, proof in replay.items():
        failures = []
        owner = {'strategy_research': 'day3', 'portfolio_research': 'd5', 'fundamental_strategy_validation': 'fundamental'}.get(namespace)
        for check in proof.get('csv_checks', []):
            for stage, hash_key in (('replay', 'before_sha256'), ('ledger', 'after_sha256')):
                p = root / HISTORY / (owner + '_' + stage) / check['path']
                if not p.is_file() or digest(p) != check[hash_key]:
                    failures.append(p.relative_to(root).as_posix())
        replay_checks[namespace] = {'status': 'PASS' if proof.get('status') == 'PASS' and proof.get('csv_checks') and not failures else 'BLOCKED',
                                    'failures': failures, 'scope': proof.get('scope')}
    write(output / 'replay_receipt_checks.json', replay_checks)
    records = []
    for node in family['records']:
        original, prior = originals[node['id']], validation[node['id']]
        links = []
        for path, line, row in tables.get((original.get('namespace'), original.get('strategy_id')), []):
            matched = row_matches(original, row)
            sibling_manifests = [v for k, v in manifests.items() if Path(k).parent == path.relative_to(root).parent]
            scoreboard_verified = any(c['status'] == 'PASS' and path.relative_to(root).as_posix() in c['paths'] for m in sibling_manifests for c in m['checks'])
            links.append({'path': path.relative_to(root).as_posix(), 'line': line, 'sha256': hashes[path.relative_to(root).as_posix()],
                          'configuration_match': matched, 'scoreboard_hash_verified': scoreboard_verified,
                          'run_manifest_status': [m['status'] for m in sibling_manifests], 'metrics': row})
        completed = bool(any(link['configuration_match'] for link in links) or stocks.get(node['id'], {}).get('execution_status') == 'COMPLETED')
        verified = any(l['configuration_match'] and l['scoreboard_hash_verified'] for l in links)
        reasons = [prior[axis]['reason'] for axis in ('data', 'backtest', 'oos') if prior[axis].get('applicable') and prior[axis]['status'] != 'PASS']
        if node['role'] in {'ORIGINAL', 'RESEARCH_MODEL'}:
            reasons.append('Original TEJ/Zipline/reference snapshot unavailable; saved Notebook output is not a parity oracle')
        r = {'id': node['id'], 'family_id': node['family_id'], 'role': node['role'], 'asset_class': node.get('asset_class'),
             'historical_counts_as_strategy': node['historical_counts_as_strategy'],
             'historical_backtest_completed': 'PASS' if completed else 'NOT_FOUND',
             'historical_evidence_verified': 'PASS' if verified else 'NOT_VERIFIABLE',
             'reproducible': replay_checks.get(original.get('namespace'), {}).get('status', 'NOT_TESTED'),
             'reproducible_scope': 'Original-owner vs observer output replay only; not data or Notebook certification',
             'current_engine_validated': 'NOT_TESTED', 'data_validated': 'BLOCKED' if node['family_id'] else 'NOT_APPLICABLE',
             'oos_validated': 'BLOCKED' if node['family_id'] else 'NOT_APPLICABLE', 'production_eligible': 'NOT_PROVEN',
             'engineering': prior['backtest']['status'] if node['family_id'] else 'NOT_APPLICABLE',
             'historical_performance': 'DIAGNOSTIC_ONLY' if completed else 'NOT_VERIFIABLE',
             'current_executable': 'YES' if original['kind'] in {'REGISTERED', 'RESEARCH_SETTING', 'FROZEN_RUNTIME'} else 'DEPENDENCY_OR_PORT_BLOCKED' if node['family_id'] else 'NOT_APPLICABLE',
             'final_status': 'BLOCKED' if node['family_id'] else 'NON_STRATEGY_TOOL',
             'blockers': sorted(set(reasons)), 'historical_metrics': prior.get('metrics'), 'historical_runs': links,
             'sources': node['sources'], 'parameters': node.get('parameters'), 'prior_validation': prior,
             'validation_plan': 'REUSE_VERIFIED_OWNER_REPLAY; independently gate full certification' if completed else 'DATA_SOURCE_ASSET_PARITY_PREFLIGHT_BEFORE_EXECUTION'}
        records.append(r)
    summary = {'family_count': len(family['families']), 'source_records': len(records),
               'historical_validation_identities': sum(r['historical_counts_as_strategy'] for r in records),
               'non_strategy_tools': sum(r['family_id'] is None for r in records),
               'research_parameters': sum(r['role'] == 'PARAMETER_CONFIG' for r in records),
               'historical_completed_identities': sum(r['historical_backtest_completed'] == 'PASS' for r in records),
               'historical_completed_families': len({r['family_id'] for r in records if r['historical_backtest_completed'] == 'PASS'}),
               'verified_historical_identities': sum(r['historical_evidence_verified'] == 'PASS' for r in records),
               'reused_reproducible_identities': sum(r['reproducible'] == 'PASS' for r in records),
               'new_valid_investment_backtests': 0, 'new_reproductions': 0, 'fresh_oos_certified': 0,
               'manifest_status_counts': dict(Counter(m['status'] for m in manifests.values()))}
    write(output / 'identity_matrix.json', {'summary': summary, 'records': records})
    write(output / 'family_source.json', family)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    audit(ROOT, args.output.resolve())
