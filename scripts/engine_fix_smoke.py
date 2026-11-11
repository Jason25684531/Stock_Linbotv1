"""Real-case check of the ledger accounting (F01, step 2.3): contrarian_pullback holding 1808 across its 2024-09-26 ex-date.

    python -m scripts.engine_fix_smoke [--strategy contrarian_pullback] [--start 2024-01-02] [--end 2024-12-31]

Runs the strategy through scripts.history_engine.execute_window on the latest snapshot (no run is saved) and writes
outputs/engine_history_fix/smoke_<strategy>_<start>_<end>.json: the trades that crossed a booked event, the booked events, the NAV on the
days around each event and the legacy trade of the same stock from the frozen baseline run, when one exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/engine_history_fix'


def legacy_trades(strategy_id, stock_id):
    from scripts import historical_foundation as hf
    baseline = json.loads((OUT / 'legacy_baseline.json').read_text(encoding='utf-8'))
    rows = []
    for run in baseline['historical_runs']:
        if run['strategy_id'] != strategy_id:
            continue
        trades = pd.read_csv(hf.RUNS / run['run_id'] / 'trades.csv', dtype={'stock_id': str})
        for t in trades[trades.stock_id == stock_id].to_dict('records'):
            rows.append({'legacy_run_id': run['run_id'], **t})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', default='contrarian_pullback')
    parser.add_argument('--start', default='2024-01-02')
    parser.add_argument('--end', default='2024-12-31')
    args = parser.parse_args(argv)
    from core.research.reference import catalog
    from scripts import historical_foundation as hf
    from scripts.history_engine import execute_window
    snap = hf.snapshot()
    cap = {c.strategy_id: c for c in catalog.capabilities()}[args.strategy]
    result = execute_window(cap, 'smoke', snap.snapshot_id, args.start, args.end)
    actions = result['corporate_actions']
    nav = result['nav'].assign(date=pd.to_datetime(result['nav'].date)).set_index('date')
    booked = actions[actions.status == 'BOOKED'] if len(actions) else actions
    crossed = []
    for event in booked.to_dict('records'):
        day = pd.Timestamp(event['date'])
        window = nav.loc[day - pd.Timedelta(days=5): day + pd.Timedelta(days=5), 'nav_base']
        trades = result['trades']
        held = trades[(trades.stock_id.astype(str) == str(event['stock_id'])) & (pd.to_datetime(trades.buy_date) < day) & (pd.to_datetime(trades.sell_date) >= day)]
        crossed.append({'event': event, 'nav_around': {d.strftime('%Y-%m-%d'): float(v) for d, v in window.items()},
                        'new_trades': held.to_dict('records'), 'legacy_trades': legacy_trades(args.strategy, str(event['stock_id']))})
    summary = {'strategy': args.strategy, 'window': [args.start, args.end], 'snapshot_id': snap.snapshot_id, 'accounting': result['accounting'],
               'ledger_version': result.get('ledger_version'), 'engine_error': result['engine_error'], 'trades': len(result['trades']),
               'events_met': len(actions), 'events_booked': len(booked), 'cash_credited': float(booked.cash_credited.sum()) if len(booked) else 0.0,
               'nav_first': float(nav.nav_base.iloc[0]), 'nav_last': float(nav.nav_base.iloc[-1]), 'open_positions': result.get('open_positions'),
               'events_crossed_by_trades': crossed, 'all_events': actions.to_dict('records')}
    target = OUT / f'smoke_{args.strategy}_{args.start}_{args.end}.json'
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('strategy', 'accounting', 'trades', 'events_met', 'events_booked', 'cash_credited', 'nav_first', 'nav_last', 'engine_error')}))
    for row in crossed:
        for t in row['new_trades']:
            print(json.dumps({'event': row['event']['kind'], 'date': row['event']['date'], 'stock': row['event']['stock_id'], 'new_profit_pct': t.get('profit_pct'),
                              'distributions': t.get('distributions'), 'legacy_profit_pct': [x.get('profit_pct') for x in row['legacy_trades']]}, default=str))
    print(json.dumps({'written': str(target)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
