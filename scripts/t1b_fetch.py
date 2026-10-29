"""T1b (Owner decision 3, 2026-10-24): official TWSE capital-reduction and par-value-change resumption tables, at most 10 requests.

RequestGate (>= 3.2 s, file state) and its own ledger (budget 10, never the OD-P6 or FinLab ledger). Every response is kept with HTTP
status, SHA-256 and retrieval time. One probe per endpoint over the whole window; a response that is not stat OK stops that endpoint.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.sources.twse import RequestGate  # noqa: E402
from core.runtime import public_data  # noqa: E402
from scripts import official_data_recovery as odr  # noqa: E402

OUT = ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official'
LEDGER = OUT / 't1b_request_ledger.jsonl'
BUDGET = 10
ENDPOINTS = {'TWTAUU': 'https://www.twse.com.tw/rwd/zh/reducation/TWTAUU',  # 減資恢復買賣參考價格
             'TWTB8U': 'https://www.twse.com.tw/rwd/zh/change/TWTB8U'}      # 變更股票面額恢復買賣參考價格
WINDOW = ('20220101', '20261023')
# TWTAUU answered 'end date before start date' to strDate: it ignored that name and used today as the start (ledger, first request)
START_PARAM = {'TWTAUU': 'startDate', 'TWTB8U': 'startDate'}  # TWTB8U with strDate returned only 2026-09 and missed 6415 (2022-07, in FinLab)


def fetch(gate, name, start, end):
    url = ENDPOINTS[name]
    start_key = START_PARAM.get(name, 'strDate')
    response = public_data.request('GET', url, params={start_key: start, 'endDate': end, 'response': 'json'}, purpose='t1b', timeout=30)
    if response.research_cache_hit:
        payload = response.json()
        return payload.get('stat'), payload
    retrieved = datetime.now(timezone.utc).isoformat(timespec='seconds')
    OUT.mkdir(parents=True, exist_ok=True)
    raw = OUT / f"{name}_{start}_{end}_{retrieved.replace(':', '')}.bin"
    raw.write_bytes(response.content)
    digest = hashlib.sha256(response.content).hexdigest()
    try:
        payload = response.json()
        stat = payload.get('stat')
    except ValueError:
        payload, stat = None, 'NOT_JSON'
    rows = len(payload.get('data') or []) if payload else 0
    odr.log(LEDGER, event='RESPONSE', dataset=name, http_status=response.status_code, bytes=len(response.content), sha256=digest, retrieved_at=retrieved,
            raw_file=raw.name, stat=stat, rows=rows)
    return stat, payload


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    gate = RequestGate(minimum_interval=3.2, state_path=LEDGER.with_suffix('.gate'))
    for name in (sys.argv[1:] or ENDPOINTS):
        stat, payload = fetch(gate, name, *WINDOW)
        print(name, stat, (payload or {}).get('fields'), len((payload or {}).get('data') or []))
    print('sent', odr.sent_count(LEDGER), 'of', BUDGET)
