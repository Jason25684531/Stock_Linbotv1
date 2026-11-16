"""Create Web publication markers only for manifest-verified, independently reconciled V2 runs."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/platform_v2'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifacts(kind: str, run_id: str) -> dict[str, Path]:
    folder = OUT / ('portfolios' if kind == 'portfolio' else 'runs') / run_id
    files = {'run.json': folder / 'run.json', 'manifest.json': folder / 'manifest.json',
             'engine_v2.py': ROOT / 'core/research/platform/engine_v2.py',
             'ledger_v2.py': ROOT / 'core/research/platform/ledger_v2.py',
             'platform_v2_engine.py': ROOT / 'scripts/platform_v2_engine.py',
             'platform_v2_ledger_audit.py': ROOT / 'scripts/platform_v2_ledger_audit.py',
             'platform_v2.py': ROOT / 'scripts/platform_v2.py'}
    if kind == 'portfolio':
        files['portfolio_v2.py'] = ROOT / 'core/research/platform/portfolio_v2.py'
    return files


def native_two_track_verified(row: dict) -> bool:
    tracks = row.get('tracks') or {}
    return (row.get('saved_publishable') is True
            and all((tracks.get(name) or {}).get('status') == 'PUBLISHABLE'
                    and not (tracks.get(name) or {}).get('missing_files')
                    and (tracks.get(name) or {}).get('evidence_mode') == 'NATIVE_SAVED_EVENTS'
                    for name in ('base', 'stress')))


def main() -> int:
    from scripts import platform_v2
    audit_path = OUT / 'acceptance/ledger/independent_ledger_audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    if (audit.get('audit_code_sha256') != digest(ROOT / 'scripts/platform_v2_ledger_audit.py')
            or audit.get('reconcile_code_sha256') != digest(ROOT / 'core/research/platform/ledger_v2.py')):
        raise SystemExit('LEDGER_AUDIT_CODE_STALE')
    verification_path = OUT / 'acceptance/phase_b/baseline_verification.json'
    verification = json.loads(verification_path.read_text(encoding='utf-8'))
    if verification.get('status') != 'PASS' or len(verification.get('rows', [])) != 16:
        raise SystemExit('BASELINE_VERIFICATION_NOT_PASS')
    baseline_ids = {row['v2_run'] for row in verification['rows'] if row.get('status') == 'PASS'}
    publication_root = OUT / 'acceptance/publication'
    accepted, rejected = [], []
    for row in audit['runs']:
        kind = 'portfolio' if row['kind'] == 'portfolios' else 'backtest'
        run_id = row['run_id']
        ok = (native_two_track_verified(row)
              and row.get('manifest_sha256') == digest(OUT / row['kind'] / run_id / 'manifest.json'))
        record = json.loads((OUT / row['kind'] / run_id / 'run.json').read_text(encoding='utf-8'))
        current_identity = (platform_v2.identity(record['parameters'], record['snapshot_id']) if kind == 'backtest'
                            else platform_v2.portfolio_identity(record['parameters'], record['snapshot_id']))
        ok = ok and current_identity == record.get('run_identity')
        if not ok:
            rejected.append({'kind': kind, 'run_id': run_id, 'reason': 'BASELINE_OR_INDEPENDENT_LEDGER_EVIDENCE_NOT_PASS'})
            continue
        paths = artifacts(kind, run_id)
        if not all(path.is_file() for path in paths.values()):
            rejected.append({'kind': kind, 'run_id': run_id, 'reason': 'MISSING_PUBLICATION_ARTIFACT'})
            continue
        marker = {'status': 'PASS', 'identity': run_id, 'kind': kind,
                  'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                  'baseline_verification': str(verification_path.relative_to(ROOT)) if run_id in baseline_ids else None,
                  'ledger_audit': str(audit_path.relative_to(ROOT)),
                  'manifest_sha256': row['manifest_sha256'],
                  'sha256': {name: digest(path) for name, path in paths.items()} |
                            {'ledger_audit.json': digest(audit_path), 'baseline_verification.json': digest(verification_path)}}
        target = publication_root / kind / f'{run_id}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding='utf-8')
        accepted.append({'kind': kind, 'run_id': run_id})
    result = {'status': 'PASS' if accepted else 'BLOCKED', 'accepted': accepted, 'rejected': rejected,
              'ledger_audit': str(audit_path.relative_to(ROOT)), 'baseline_verification': str(verification_path.relative_to(ROOT))}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
