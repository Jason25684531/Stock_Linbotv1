"""Read-only capture of this repository's Windows scheduled tasks (design D11).

Runs `schtasks /query /fo csv /v` (never /create or /change), keeps the tasks whose action points into this repository and writes
outputs/market_data_autonomy/scheduler/tasks.json. Column positions are used because the headers are localized. Only the Owner sets
os_task_verified.
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/market_data_autonomy/scheduler/tasks.json'
FIELDS = ('host', 'name', 'next_run', 'status', 'logon_mode', 'last_run', 'last_result', 'author', 'action')


def parse(text) -> list:
    marker = str(ROOT).lower()
    tasks, seen = [], set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) < len(FIELDS):
            continue
        item = dict(zip(FIELDS, (value.strip() for value in row)))
        if marker not in item['action'].replace('/', '\\').lower() or (item['name'], item['action']) in seen:
            continue
        seen.add((item['name'], item['action']))
        tasks.append({k: item[k] for k in FIELDS if k not in ('host', 'author')})
    return tasks


def capture() -> dict:
    done = subprocess.run(['schtasks', '/query', '/fo', 'csv', '/v'], capture_output=True)
    text = done.stdout.decode('mbcs' if sys.platform == 'win32' else 'utf-8', errors='replace')
    result = {'captured_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'command': 'schtasks /query /fo csv /v',
              'exit_code': done.returncode, 'tasks': parse(text)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result


if __name__ == '__main__':
    print(json.dumps(capture(), ensure_ascii=True, indent=1))
