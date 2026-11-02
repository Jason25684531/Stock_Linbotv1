"""OD-P6 (approved by the Owner 2026-10-24): TWT49U detail of the 385 events the cached table cannot split (220 rights-and-dividend,
165 rights offerings). Every request goes through RequestGate (>= 3.2 s, file state), is reserved in the ledger against a hard budget of 385,
and logs gate_gap_seconds. `probe` sends exactly one attempt on the primary endpoint and stops on any failure; `run` starts only after a
VERIFIED probe in the current round. public_data rejects every detail payload until its schema is confirmed, so today nothing is certified.
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
from core.runtime import public_data  # noqa: E402
from scripts import official_data_recovery as odr  # noqa: E402
from scripts.p0_p4_research import TWT49U, split_event  # noqa: E402

OUT = ROOT / 'outputs/p0_p4/2026-10-24'
CACHE = OUT / 'od_p6_twt49u_detail'
LEDGER = OUT / 'od_p6_request_ledger.jsonl'
BUDGET = 385
MIN_INTERVAL = 3.2
ENDPOINTS = ('https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail', 'https://www.twse.com.tw/exchangeReport/TWT49UDetail')  # primary first; the alias is never a fallback
MAX_CONSECUTIVE_FAILURES = 3
DEV_END = '20251231'  # 2026 events are out of the development window and never requested
ROUND_START, ROUND_CAP = '2026-10-30', 342  # 2026-10-30-verified-strategy-admission: probe included


def round_rows():
    """This round's od_p6 attempts from the durable budget audit, so separate probe/run invocations share one cap."""
    from core.runtime import research_budget
    return [row for row in research_budget.audit_rows() if row['job'] == 'od_p6' and row['reserved_at'] >= ROUND_START]


def round_used() -> int:
    return len(round_rows())


def targets() -> list[tuple[str, str]]:
    """(stock_id, yyyymmdd) of every event the cached table cannot fully split, from its own detail-link field."""
    payload = json.loads(TWT49U.read_text(encoding='utf-8'))
    found = []
    for r in payload['data']:
        if r[1].isdigit() and len(r[1]) == 4 and split_event(r[6], parse_number(r[3]), parse_number(r[4]), parse_number(r[10]))['status'] != 'SPLIT_PROVEN':
            stock, day = r[11].split(',')
            if day <= DEV_END:
                found.append((stock, day))
    return found


def fetch(gate, url, stock, day):
    path = CACHE / f'{stock}_{day}.json'
    # The common boundary owns reservation, alias identity, persistent pacing and verified cache.
    response = None
    try:
        response = public_data.request('GET', url, params={'STK_NO': stock, 'T': day, 'response': 'json'}, purpose='od_p6', timeout=30)
        if response.research_cache_hit:
            return 'CACHED'
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


def report(out_dir):
    """Write the round outcome from the budget audit; no request is made."""
    from core.runtime import research_budget
    rows = [r for r in research_budget.audit_rows() if r['job'] == 'od_p6']
    attempts = [r for r in rows if r['reserved_at'] >= ROUND_START]
    requested = {r['request_key'] for r in attempts}  # read once, not per target
    verified = sum(1 for r in attempts if r['outcome'] == 'VERIFIED')
    summary = {'round_start': ROUND_START, 'round_cap': ROUND_CAP, 'round_used': len(attempts),
               'od_p6_budget': research_budget.status()['jobs']['od_p6'], 'development_targets': len(targets()),
               'attempts': attempts, 'not_requested_targets': [t for t in targets() if key(*t) not in requested],
               'response_sha256_counts_all_time': {sha: sum(r['sha256'] == sha for r in rows) for sha in {r['sha256'] for r in rows if r['sha256']}},
               'verified_responses': verified, 'status': 'PASS' if verified else 'BLOCKED',
               'reason': None if verified else 'Every detail response is the busy page or fails the unconfirmed-schema gate; '
                         'no field can be certified and the run was not started.'}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'summary.json').open('x', encoding='utf-8') as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, default=str)
    return summary


def gate():
    return RequestGate(minimum_interval=MIN_INTERVAL, state_path=LEDGER.with_suffix('.gate'))


def probe(target=None):
    """Exactly one attempt on the primary endpoint for the given or first target; any failure stops (no alias fallback)."""
    stock, day = target or targets()[0]
    if (stock, day) not in targets():
        raise SystemExit('probe target outside the development-window OD-P6 targets')
    if round_used() >= ROUND_CAP:
        print('STOPPED: round cap reached', ROUND_CAP)
        return 'STOPPED'
    state = fetch(gate(), ENDPOINTS[0], stock, day)
    print('ENDPOINT_OK' if state == 'OK' else 'NO_ENDPOINT_CONFIRMED: stop, report to the Owner', ENDPOINTS[0], state)
    return state


def key(stock, day):
    return public_data.context(ENDPOINTS[0], params={'STK_NO': stock, 'T': day, 'response': 'json'}, purpose='od_p6').key


def run(url):
    if not any(r['outcome'] == 'VERIFIED' for r in round_rows()):
        print('REFUSED: no VERIFIED probe in this round')  # each run attempt would only spend budget on the schema gate
        return 'REFUSED'
    g, failures, results = gate(), 0, {}
    for stock, day in targets():
        if round_used() >= ROUND_CAP:
            print('STOPPED: round cap reached', ROUND_CAP, results)
            return results
        state = fetch(g, url, stock, day)
        results[state] = results.get(state, 0) + 1
        failures = failures + 1 if state == 'FAILED' else 0
        if failures >= MAX_CONSECUTIVE_FAILURES:
            print('STOPPED: three consecutive failures', results)
            return results
    print('DONE', results, 'sent', odr.sent_count(LEDGER))
    return results


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('targets', 'probe', 'reprobe', 'run', 'report'))
    parser.add_argument('--url')
    parser.add_argument('--out', help='report: new output directory')
    parser.add_argument('--target', help='probe: STOCK:YYYYMMDD inside the development-window targets')
    args = parser.parse_args()
    if args.command == 'run' and args.url not in ENDPOINTS:
        parser.error('--url must be one of the registered TWT49UDetail endpoints')
    if args.command == 'targets':
        t = targets()
        print(len(t), t[:3])
    elif args.command == 'report':
        print(json.dumps({k: v for k, v in report(args.out).items() if k != 'attempts'}, ensure_ascii=False, default=str))
    elif args.command in ('probe', 'reprobe'):  # reprobe (Owner decision 2) is the same single primary-endpoint attempt under the round cap
        probe(tuple(args.target.split(':')) if args.target else None)
    else:
        run(args.url)
