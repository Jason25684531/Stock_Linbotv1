"""Run the whole test suite and record the production operation folder before and after (path, SHA-256, size, mtime of every file)."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPERATION = ROOT / 'outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1'
RECORD = ROOT / 'outputs/dual_track_validation/2026-10-23/closure/test_isolation_record.json'


def state():
    files = {p.relative_to(OPERATION).as_posix(): {'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(OPERATION.rglob('*')) if p.is_file()}
    return {'at': datetime.now().isoformat(timespec='seconds'), 'folder': str(OPERATION.resolve()), 'files': files}


if __name__ == '__main__':
    if RECORD.exists():
        raise SystemExit(f'{RECORD} exists; evidence is never overwritten')
    before = state()
    run = subprocess.run([sys.executable, '-m', 'pytest', 'test', '-q', '-p', 'no:cacheprovider', '-x', '--maxfail=50'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    after = state()
    changed = sorted(k for k in before['files'].keys() | after['files'].keys() if before['files'].get(k) != after['files'].get(k))
    RECORD.write_text(json.dumps({'command': 'python -m pytest test', 'returncode': run.returncode, 'summary': run.stdout.strip().splitlines()[-1], 'before': before, 'after': after,
                                  'changed_files': changed, 'status_file': 'fundamental_production_status.json',
                                  'status_file_unchanged': before['files'].get('fundamental_production_status.json') == after['files'].get('fundamental_production_status.json')},
                                 indent=1), encoding='utf-8')
    print(run.stdout.strip().splitlines()[-1], '| changed:', changed)
