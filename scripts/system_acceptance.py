"""Explicit-only final acceptance tooling. Never runs production jobs or sends notifications."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/final_system_acceptance'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def sources():
    paths = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    return {p: digest(ROOT / p) for p in sorted(set(paths))
            if (ROOT / p).is_file() and Path(p).suffix in ('.py', '.html', '.js', '.css', '.bat', '.toml', '.ini')}


def baseline():
    if (OUT / 'baseline.json').exists():
        raise RuntimeError('Baseline already exists; never overwrite original evidence')
    old = json.loads((ROOT / 'outputs/strategy_v4/acceptance/baseline.json').read_text(encoding='utf-8'))
    protected = {p: digest(ROOT / p) for p in old['protected'] if (ROOT / p).is_file()}
    historical_drift = [p for p, sha in old['protected'].items() if protected.get(p) != sha]
    # Include V4 deliverables as well as its inherited protected V1–V3 set.
    for path in (ROOT / 'outputs/strategy_v4').rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.log', '.tmp'):
            protected[path.relative_to(ROOT).as_posix()] = digest(path)
    from scripts.data_readiness_checkpoint import backup
    backup(['app/web_server.py', 'app/platform_v2_routes.py', 'templates/platform_system.html',
            'jobs/run_research_platform.py', 'scripts/task_controller.py', 'execution/morning_run.bat'], out=OUT / 'prechange')
    write('baseline.json', {'at': now(), 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                           'working_tree': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
                           'sources': sources(), 'protected': protected, 'historical_protected_count': len(old['protected']),
                           'historical_drift': historical_drift})
    (OUT / 'initial.diff').write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT))


def command(name, argv, timeout=3600):
    started = now()
    identity = sources()
    log = OUT / 'logs' / f'{name}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', STOCK_LINBOT_INGESTION_DISABLED='1')
    with log.open('w', encoding='utf-8') as stream:
        try:
            proc = subprocess.run(argv, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            code = proc.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(f'{type(error).__name__}: {error}\n')
            code = -1
    result = {'command': argv, 'started_at': started, 'ended_at': now(), 'exit_code': code,
              'status': 'PASS' if code == 0 else 'FAIL', 'log': str(log.relative_to(ROOT)),
              'source_identity': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    write(f'checks/{name}.json', result)
    return result


def protected():
    base = read('baseline.json')
    differences = [p for p, sha in base['protected'].items() if not (ROOT / p).is_file() or digest(ROOT / p) != sha]
    result = {'at': now(), 'status': 'MATCH' if not differences else 'FAIL', 'files': len(base['protected']), 'differences': differences}
    write('checks/protected.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['baseline', 'protected', 'command'])
    parser.add_argument('--name')
    parser.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'baseline':
        baseline()
    elif args.action == 'protected':
        print(json.dumps(protected()))
    else:
        argv = args.args[1:] if args.args[:1] == ['--'] else args.args
        if not args.name or not argv:
            parser.error('command requires --name before action and a command after --')
        result = command(args.name, argv)
        print(json.dumps(result))
        return int(result['status'] != 'PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
