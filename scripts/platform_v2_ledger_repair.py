"""One-off, isolated repair of only V2 runs lacking independent stress evidence."""
from __future__ import annotations

import json
import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest import run_store
from scripts import platform_v2

STRATEGY_RUNS = (
    '20261113T045528Z-16d7ad51', '20261113T050634Z-18dc0bdc',
    '20261113T050953Z-5be3bb50', '20261113T052336Z-f1a0c818',
    '20261113T054726Z-29c9987b', '20261113T054901Z-b58778e6',
    '20261113T055204Z-fd0ee8f0', '20261113T062228Z-1740087b',
)
PORTFOLIO_RUNS = ('20261113T070948Z-58f21765', '20261113T073357Z-0b91a272')
REMAINING_RUNS = (
    '20261113T045358Z-66d85edc', '20261113T045443Z-c9de6a0a',
    '20261113T045613Z-c128dfff', '20261113T052431Z-bcc46661',
    '20261113T053548Z-671cd52c', '20261113T054635Z-4004242a',
    '20261113T062358Z-d4c99e5f', '20261113T062447Z-26e03a34',
)
LOG = ROOT / 'outputs/platform_v2/acceptance/ledger/repair_runs.jsonl'


def reusable(record, files, audit, manifest_sha256):
    """Only skip a repair when the immutable files replay successfully from native evidence."""
    required = {'fills_stress.csv', 'daily_ledger_stress.csv', 'positions_daily_stress.csv',
                'corporate_actions_stress.csv', 'open_positions_stress.json'}
    tracks = audit.get('tracks') or {}
    return (required <= set(files) and record.get('publishable') is True
            and audit.get('saved_publishable') is True
            and audit.get('manifest_sha256') == manifest_sha256
            and all((tracks.get(track) or {}).get('status') == 'PUBLISHABLE'
                    and not (tracks.get(track) or {}).get('missing_files')
                    and (tracks.get(track) or {}).get('evidence_mode') == 'NATIVE_SAVED_EVENTS'
                    for track in ('base', 'stress'))
            and all((record.get('reconciliation') or {}).get(track, {}).get('status') == 'PUBLISHABLE'
                    for track in ('base', 'stress')))


def run(kind, old_id):
    from scripts.platform_v2_ledger_audit import audit_run
    source = ROOT / 'outputs/platform_v2' / kind
    old = run_store.load_run(old_id, root=source)['run']
    params = old['parameters']
    snapshot = old['snapshot_id']
    identity = platform_v2.identity(params, snapshot) if kind == 'runs' else platform_v2.portfolio_identity(params, snapshot)
    existing = platform_v2.lookup(identity, source)
    required = {'fills_stress.csv', 'daily_ledger_stress.csv', 'positions_daily_stress.csv',
                'corporate_actions_stress.csv', 'open_positions_stress.json'}
    reusable_id = None
    if existing:
        candidate = run_store.load_run(existing, root=source)
        audit = audit_run(source / existing)
        manifest_sha256 = run_store.file_hash(source / existing / 'manifest.json')
        if reusable(candidate['run'], candidate['files'], audit, manifest_sha256):
            reusable_id = existing
    new_id = reusable_id or (platform_v2.run_one(params, snapshot, root=source, force=True) if kind == 'runs'
                          else platform_v2.portfolio_run(params, snapshot, root=source, force=True))
    new = run_store.load_run(new_id, root=source)['run']
    return {'kind': kind, 'old_run_id': old_id, 'new_run_id': new_id,
            'strategy_id': old['strategy_id'], 'same_identity': old['run_identity'] == new['run_identity'],
            'publishable': new['publishable'], 'reconciliation': new['reconciliation']}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--remaining', action='store_true')
    args = parser.parse_args(argv)
    if args.workers != 4:
        parser.error('ledger repair requires four isolated workers')
    selected_runs = REMAINING_RUNS if args.remaining else STRATEGY_RUNS
    jobs = [('runs', x) for x in selected_runs] + ([] if args.remaining else [('portfolios', x) for x in PORTFOLIO_RUNS])
    LOG.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=get_context('spawn')) as pool:
        pending = {pool.submit(run, *job): job for job in jobs}
        for future in as_completed(pending):
            kind, old_id = pending[future]
            try:
                result = future.result()
            except Exception as error:
                result = {'kind': kind, 'old_run_id': old_id, 'error_type': type(error).__name__,
                          'error': str(error)[:1000]}
            result['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
            result['status'] = ('PASS' if result.get('publishable') is True and result.get('same_identity') is True
                                and all((result.get('reconciliation') or {}).get(track, {}).get('status') == 'PUBLISHABLE'
                                        for track in ('base', 'stress')) else 'FAIL')
            results.append(result)
            with LOG.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
            print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    passed = sum(row['status'] == 'PASS' for row in results)
    status = 'DONE' if passed == len(jobs) else 'PARTIAL' if passed else 'FAILED'
    print(json.dumps({'status': status, 'repaired': passed, 'failed': len(jobs) - passed}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
