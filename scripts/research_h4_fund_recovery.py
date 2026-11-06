"""h4-fundamental-recovery-v1 (design.md): fetch filing lists and statements of the 15 fixed companies through the one public transport.

Commands: probe | fetch. Every attempt is logged with URL, form, time, status and SHA256; responses are stored once under RAW."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import public_data

OUT = ROOT / 'outputs/research_mode/h4_fund_recovery_v1'
RAW = OUT / 'raw'
LOG = OUT / 'fetch_log.jsonl'
T57 = 'https://doc.twse.com.tw/server-java/t57sb01'
T164 = 'https://mopsov.twse.com.tw/mops/web/ajax_t164sb04'


def listing_form(ticker, roc_year=''):
    return {'id': '', 'key': '', 'step': '1', 'co_id': ticker, 'year': str(roc_year), 'seamon': '', 'mtype': 'A', 'dtype': 'AI1'}


def statement_form(ticker, roc_year, season):
    return {**public_data.FUND_T164, 'co_id': ticker, 'year': str(roc_year), 'season': f'{season:02d}'}


def get(name, url, form):
    """One logged attempt; returns the stored path or None. Cached responses cost nothing."""
    RAW.mkdir(parents=True, exist_ok=True)
    entry = {'file': name, 'url': url, 'form': form, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    try:
        response = public_data.request('POST', url, data=form)
        target = RAW / name
        if not target.exists():
            target.write_bytes(response.content)
        entry.update(result='VERIFIED', status=response.status_code, sha256=hashlib.sha256(response.content).hexdigest(),
                     cache_hit=bool(getattr(response, 'research_cache_hit', False)))
    except Exception as exc:  # ponytail: the transport already recorded and debited the attempt; keep the reason
        entry.update(result='FAILED', error=f'{type(exc).__name__}: {str(exc)[:300]}')
    with LOG.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def probe():
    return [get('t57sb01_2448_all.html', T57, listing_form('2448')), get('t164sb04_2448_108_1.html', T164, statement_form('2448', 108, 1))]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('probe',))
    print(json.dumps({'probe': probe}[parser.parse_args().command](), ensure_ascii=False, indent=1))
