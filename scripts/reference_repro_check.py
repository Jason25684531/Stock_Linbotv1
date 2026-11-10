"""Compare two reference batches run by run (read-only): same strategies, trades and daily NAV.

Trades must match exactly on stock, buy and sell date, and shares; prices, cost, profit and NAV within rtol 1e-9 and atol 1e-12
(AGENTS.md Verification). A strategy RUN in one batch and not the other is reported, never skipped silently.

CLI: python scripts/reference_repro_check.py BATCH_A BATCH_B [--out FILE]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'reference_mode'
EXACT = ['stock_id', 'buy_date', 'sell_date', 'shares']
CLOSE = ['buy_price', 'sell_price', 'cost', 'profit_pct']


def _rows(batch):
    report = json.loads((OUT / 'batches' / batch / 'report.json').read_text(encoding='utf-8'))
    return {r['strategy_id']: r for r in report['rows']}


def _close(a, b) -> bool:
    return bool(np.isclose(a.astype(float), b.astype(float), rtol=1e-9, atol=1e-12, equal_nan=True).all())


def _trades(path) -> pd.DataFrame:
    try:
        return pd.read_csv(path / 'trades.csv', dtype={'stock_id': str})
    except pd.errors.EmptyDataError:  # a run with no trades (macd_cross in the window) writes an empty file
        return pd.DataFrame(columns=EXACT + CLOSE)


def compare_runs(run_a, run_b) -> dict:
    a, b = OUT / 'runs' / run_a, OUT / 'runs' / run_b
    ta, tb = _trades(a), _trades(b)
    na, nb = (pd.read_csv(p / 'nav.csv') for p in (a, b))
    out = {'trades': [len(ta), len(tb)], 'nav_days': [len(na), len(nb)]}
    out['trades_exact'] = len(ta) == len(tb) and ta[EXACT].astype(str).equals(tb[EXACT].astype(str))
    out['trades_close'] = out['trades_exact'] and all(_close(ta[c], tb[c]) for c in CLOSE if c in ta)
    out['nav_dates'] = len(na) == len(nb) and na.date.equals(nb.date)
    out['nav_close'] = out['nav_dates'] and all(_close(na[c], nb[c]) for c in na.columns if c != 'date')
    if out['nav_dates']:
        out['nav_max_abs_diff'] = float(max((na[c] - nb[c]).abs().max() for c in na.columns if c != 'date'))
    out['pass'] = out['trades_close'] and out['nav_close']
    return out


def compare(batch_a, batch_b) -> dict:
    ra, rb = _rows(batch_a), _rows(batch_b)
    result = {'batch_a': batch_a, 'batch_b': batch_b, 'strategies': {}}
    for sid in ra.keys() | rb.keys():
        x, y = ra.get(sid, {}), rb.get(sid, {})
        entry = {'state': [x.get('state'), y.get('state')], 'run_id': [x.get('run_id'), y.get('run_id')],
                 'run_identity_equal': x.get('run_identity') == y.get('run_identity')}
        if x.get('run_id') and y.get('run_id'):
            entry.update(compare_runs(x['run_id'], y['run_id']))
        result['strategies'][sid] = entry
    compared = [e for e in result['strategies'].values() if 'pass' in e]
    result['compared'] = len(compared)
    result['reproducible'] = sum(e['pass'] for e in compared)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('batch_a')
    parser.add_argument('batch_b')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    result = compare(args.batch_a, args.batch_b)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    print(text)
    return 0 if result['compared'] and result['reproducible'] == result['compared'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
