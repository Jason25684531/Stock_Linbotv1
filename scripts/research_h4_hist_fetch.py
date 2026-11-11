"""h4-historical-data-unlock-v1: the fixed 2015-2022 TWSE request plan (design D1) through the one public research transport.

Every request goes through core.runtime.public_data.request (allowlist, budget job h4_hist, 3.2 s host gate, cooldown, verified cache).
Already cached responses cost nothing. The batch stops on any cooldown or after 5 consecutive failures; rerunning resumes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import public_data

TWSE = 'https://www.twse.com.tw/rwd/zh'
OUT = ROOT / 'outputs/research_mode/h4_hist_unlock_v1'
RAW = ROOT / 'outputs/research_mode/hist_raw_v1'
LOG = OUT / 'fetch_log.jsonl'


def plan():
    """(file name, url, params) in request order: events first, then every weekday quote."""
    window = {'startDate': '20150101', 'endDate': '20211231', 'response': 'json'}
    items = [('TWT49U_20150101_20221231.json', f'{TWSE}/exRight/TWT49U', {'startDate': '20150101', 'endDate': '20221231', 'response': 'json'}),
             ('TWTAUU_20150101_20211231.json', f'{TWSE}/reducation/TWTAUU', window),
             ('TWTB8U_20150101_20211231.json', f'{TWSE}/change/TWTB8U', window)]
    for day in pd.bdate_range('2015-01-01', '2021-12-29'):
        d = day.strftime('%Y%m%d')
        items.append((f'MI_INDEX_{d}.json', f'{TWSE}/afterTrading/MI_INDEX', {'date': d, 'type': 'ALL', 'response': 'json'}))
    return items


def fetch(only=None, limit=None, items=None):
    """items: an explicit (file name, url, params) list instead of plan() (the 2026-11-10 Saturday plan reuses this transport and log)."""
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    failures = sent = 0
    summary = {'cached': 0, 'sent': 0, 'failed': 0, 'stopped': None}
    for name, url, params in (plan() if items is None else items):
        if only and name not in only:
            continue
        if limit is not None and sent >= limit:
            summary['stopped'] = f'LIMIT_{limit}'
            break
        entry = {'file': name, 'url': url, 'params': params, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        try:
            response = public_data.request('GET', url, params=params)
            hit = bool(getattr(response, 'research_cache_hit', False))
            entry.update(status=response.status_code, sha256=hashlib.sha256(response.content).hexdigest(), cache_hit=hit, result='VERIFIED')
            target = RAW / name
            if not target.exists():
                target.write_bytes(response.content)
            summary['cached' if hit else 'sent'] += 1
            sent += 0 if hit else 1
            failures = 0
        except Exception as exc:  # ponytail: the transport already recorded the debit; keep the reason and decide whether to stop
            entry.update(result='FAILED', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            summary['failed'] += 1
            sent += 1
            failures += 1
        if not entry.get('cache_hit'):
            with LOG.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        error = entry.get('error', '')
        if 'COOLDOWN' in error or 'BUDGET' in error or failures >= 5:
            summary['stopped'] = error[:200] or 'FIVE_CONSECUTIVE_FAILURES'
            break
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='*', help='file names from the plan (pilot)')
    parser.add_argument('--limit', type=int, help='stop after this many network attempts')
    args = parser.parse_args()
    fetch(set(args.only) if args.only else None, args.limit)
