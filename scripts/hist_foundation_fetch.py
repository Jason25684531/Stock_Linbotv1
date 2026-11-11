"""Owner-approved plan 2026-11-10 (change 2026-11-10-engine-accounting-and-history-gaps-v1): the eight TWSE Saturday make-up sessions.

    python -m scripts.hist_foundation_fetch [--limit N]

Same transport, raw directory and fetch log as scripts/research_h4_hist_fetch.py; the budget job is hist_foundation_quotes (cap 8, retries
included: core.runtime.public_data.HIST_SATURDAYS). A day already on disk is skipped before any request. Prints one JSON summary line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime.public_data import HIST_SATURDAYS
from scripts import research_h4_hist_fetch as h4

TWSE = 'https://www.twse.com.tw/rwd/zh'


def plan(existing=None):
    existing = h4.RAW if existing is None else Path(existing)
    return [(f'MI_INDEX_{d}.json', f'{TWSE}/afterTrading/MI_INDEX', {'date': d, 'type': 'ALL', 'response': 'json'})
            for d in sorted(HIST_SATURDAYS) if not (existing / f'MI_INDEX_{d}.json').exists()]


def on_disk() -> dict:
    """{date: {'file', 'sha256', 'bytes'}} for every Saturday file present in the raw directory."""
    out = {}
    for d in sorted(HIST_SATURDAYS):
        path = h4.RAW / f'MI_INDEX_{d}.json'
        if path.exists():
            raw = path.read_bytes()
            out[d] = {'file': str(path.relative_to(ROOT).as_posix()), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, help='stop after this many network attempts')
    args = parser.parse_args(argv)
    items = plan()
    summary = h4.fetch(items=items, limit=args.limit) if items else {'cached': 0, 'sent': 0, 'failed': 0, 'stopped': None}
    print(json.dumps({'planned': len(items), **summary, 'on_disk': on_disk()}, ensure_ascii=False), flush=True)
    return 0 if summary['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
