"""Read-only replay of stored governance, baseline, portfolio and V4 ledger evidence."""
import importlib.util
import json
from pathlib import Path

from scripts.system_acceptance import ROOT, OUT, write, now


def main():
    from scripts import platform_v2 as p2, historical_foundation as hf, strategy_intelligence as si
    from scripts.platform_v2_ledger_audit import audit_run
    from core.backtest import run_store
    governance = p2.governance(write=False)
    write('checks/governance.json', governance)
    evaluation = hf.latest_evaluation()
    plan = {r['strategy_id']: r for r in hf.plan(evaluation)}
    baseline, ledgers = [], []
    for params in p2.baseline_params():
        sid = params['strategy_id']
        run_id = p2.lookup(p2.identity(params, evaluation['snapshot_id']))
        loaded = run_store.load_run(run_id, root=p2.RUNS)
        first = si.load_run(plan[sid]['run_id'])
        second = {key: run_store.read_frame(loaded, key) for key in ('nav', 'trades')}
        empty = second['trades'].iloc[:0]
        differences = p2.compare(first | {'trades_stress': empty}, second | {'trades_stress': empty})
        baseline.append({'strategy_id': sid, 'v1': plan[sid]['run_id'], 'v2': run_id, 'differences': differences,
                         'status': 'PASS' if not differences else 'FAIL'})
        ledgers.append(audit_run(p2.RUNS / run_id))
    for path in sorted((p2.OUT / 'portfolios').glob('2*')):
        loaded = run_store.load_run(path.name, root=path.parent)
        if loaded['run'].get('publishable'):
            ledgers.append(audit_run(path))
    # Import only the independent audit function; never invoke its mutating main().
    path = ROOT / 'outputs/strategy_v4/acceptance/independent_verify.py'
    spec = importlib.util.spec_from_file_location('v4_readonly_audit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bars = hf.snapshot(evaluation['snapshot_id']).frame('bars')
    close = bars.set_index(['stock_id', 'trade_date'])['close'].to_dict()
    v4 = []
    for result_path in sorted((ROOT / 'outputs/strategy_v4/results').glob('*.json')):
        result = json.loads(result_path.read_text(encoding='utf-8'))
        for label in ('original', 'fixed'):
            item = result[label]
            loaded = run_store.load_run(item['run_id'], root=ROOT / Path(item['source']).parent)
            v4.append(module.audit(loaded, close))
    failed = [r for r in ledgers if any(t['status'] != 'PUBLISHABLE' for t in r['tracks'].values())]
    from app import platform_v2_routes as routes
    for row in failed:
        loaded = run_store.load_run(row['run_id'], root=p2.OUT / row['kind'])
        row['web_publishable'] = routes._safe_run(loaded['run'], p2.OUT / row['kind'])['publishable']
    unsafe = [r for r in failed if r['web_publishable']]
    payload = {'at': now(), 'status': 'PASS' if governance['status'] == 'MATCH' and not unsafe and all(r['status'] == 'PASS' for r in baseline) else 'FAIL',
               'baseline': baseline, 'ledgers': ledgers, 'v4_independent': v4, 'failed_ledger': failed}
    write('reconciliation_details.json', payload)
    print(json.dumps({'status': payload['status'], 'baseline': len(baseline), 'ledgers': len(ledgers), 'v4': len(v4)}))
    return int(payload['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
