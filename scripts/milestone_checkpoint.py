"""Stage checkpoints for change 2026-11-09-market-data-autonomy-and-reference-research-v1 (design D13).

python scripts/milestone_checkpoint.py R1 --tests test/a.py test/b.py --evidence path ... [--level ENGINEERING_PASS ...] [--note text]
Runs the stage's tests, then writes outputs/market_data_autonomy/checkpoints/<stage>.json with the command, exit code, summary line,
evidence paths with SHA256 and the result levels claimed. A stage whose tests fail is written with passed=false.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/market_data_autonomy/checkpoints'


def checkpoint(stage, tests, evidence, levels=(), note='') -> dict:
    command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *tests]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    summary = next((line for line in reversed(done.stdout.splitlines()) if ' passed' in line or ' failed' in line or ' error' in line), '')
    record = {'stage': stage, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'command': ' '.join(command[1:]),
              'exit_code': done.returncode, 'passed': done.returncode == 0, 'summary': summary.strip('= '),
              'evidence': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() if (ROOT / p).is_file() else None for p in evidence},
              'levels': list(levels), 'note': note}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{stage}.json').write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('stage')
    parser.add_argument('--tests', nargs='*', default=[])
    parser.add_argument('--evidence', nargs='*', default=[])
    parser.add_argument('--level', nargs='*', default=[])
    parser.add_argument('--note', default='')
    args = parser.parse_args(argv)
    record = checkpoint(args.stage, args.tests, args.evidence, args.level, args.note)
    print(json.dumps({k: record[k] for k in ('stage', 'passed', 'summary')}, ensure_ascii=True))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
