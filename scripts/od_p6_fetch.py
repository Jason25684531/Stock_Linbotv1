"""OD-P6 (approved by the Owner 2026-10-24): TWT49U detail of the 385 events the cached table cannot split (220 rights-and-dividend,
165 rights offerings). Every request goes through RequestGate (>= 3.2 s, file state), is reserved in the ledger against a hard budget of 385,
and logs gate_gap_seconds. `probe` sends one request to confirm the endpoint; `run` fetches the rest with the confirmed endpoint.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.adjustment.normalization import parse_number  # noqa: E402
from core.research.sources.twse import RequestGate  # noqa: E402
from scripts import official_data_recovery as odr  # noqa: E402
from scripts.p0_p4_research import TWT49U, split_event  # noqa: E402

OUT = ROOT / 'outputs/p0_p4/2026-10-24'
CACHE = OUT / 'od_p6_twt49u_detail'
LEDGER = OUT / 'od_p6_request_ledger.jsonl'
BUDGET = 385
MIN_INTERVAL = 3.2
ENDPOINTS = ('https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail', 'https://www.twse.com.tw/exchangeReport/TWT49UDetail')  # first that answers stat OK
MAX_CONSECUTIVE_FAILURES = 3


def targets() -> list[tuple[str, str]]:
    """(stock_id, yyyymmdd) of every event the cached table cannot fully split, from its own detail-link field."""
    payload = json.loads(TWT49U.read_text(encoding='utf-8'))
    found = []
    for r in payload['data']:
        if r[1].isdigit() and len(r[1]) == 4 and split_event(r[6], parse_number(r[3]), parse_number(r[4]), parse_number(r[10]))['status'] != 'SPLIT_PROVEN':
            stock, day = r[11].split(',')
            found.append((stock, day))
    return found


def fetch(gate, url, stock, day):
    path = CACHE / f'{stock}_{day}.json'
    if path.exists():
        return 'CACHED'
    odr.reserve(LEDGER, 'TWT49U_DETAIL', day, f'{url}?STK_NO={stock}&T={day}', budget=BUDGET)
    gate.wait()
    odr.log(LEDGER, event='RELEASED', dataset='TWT49U_DETAIL', stock_id=stock, date=day, gate_gap_seconds=gate.last_gap, gate_release_wall=gate.last_release_wall)
    response = None
    try:
        response = requests.get(url, params={'STK_NO': stock, 'T': day, 'response': 'json'}, timeout=30)
        (CACHE / '_responses').mkdir(parents=True, exist_ok=True)
        raw = CACHE / '_responses' / f"{stock}_{day}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.bin"
        raw.write_bytes(response.content)  # every response is kept, success or not
        odr.log(LEDGER, event='RESPONSE', stock_id=stock, date=day, http_status=response.status_code, bytes=len(response.content),
                sha256=hashlib.sha256(response.content).hexdigest(), raw_file=raw.name, content_type=response.headers.get('Content-Type'))
        payload = response.json()
        if payload.get('stat') != 'OK':
            raise ValueError(f"stat {payload.get('stat')!r}")
    except Exception as exc:
        odr.log(LEDGER, event='RESULT', dataset='TWT49U_DETAIL', stock_id=stock, date=day, outcome=f'FAILED:{type(exc).__name__}:{str(exc)[:80]}')
        return 'FAILED'
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    odr.log(LEDGER, event='RESULT', dataset='TWT49U_DETAIL', stock_id=stock, date=day, outcome='RECEIVED')
    return 'OK'


def gate():
    return RequestGate(minimum_interval=MIN_INTERVAL, state_path=LEDGER.with_suffix('.gate'))


def probe():
    """At most one request per candidate endpoint, on the first target; stops at the first that answers stat OK."""
    stock, day = targets()[0]
    g = gate()
    for url in ENDPOINTS:
        if fetch(g, url, stock, day) == 'OK':
            print('ENDPOINT_OK', url, (CACHE / f'{stock}_{day}.json').read_text(encoding='utf-8')[:1500])
            return
    print('NO_ENDPOINT_CONFIRMED: stop, report to the Owner')


def run(url):
    g, failures, results = gate(), 0, {}
    for stock, day in targets():
        state = fetch(g, url, stock, day)
        results[state] = results.get(state, 0) + 1
        failures = failures + 1 if state == 'FAILED' else 0
        if failures >= MAX_CONSECUTIVE_FAILURES:
            print('STOPPED: three consecutive failures', results)
            return
    print('DONE', results, 'sent', odr.sent_count(LEDGER))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('targets', 'probe', 'reprobe', 'run'))
    parser.add_argument('--url')
    args = parser.parse_args()
    if args.command == 'targets':
        t = targets()
        print(len(t), t[:3])
    elif args.command == 'probe':
        probe()
    elif args.command == 'reprobe':  # Owner decision 2: one request, same endpoint and parameters, >= 30 min after the last one
        stock, day = targets()[0]
        print(fetch(gate(), ENDPOINTS[0], stock, day))
    else:
        run(args.url)
