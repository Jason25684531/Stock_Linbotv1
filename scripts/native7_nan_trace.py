"""Native-7 rules-only NaN trace: replay one strategy and stop at the first non-finite daily equity, dumping the evidence (no fill, no repair).

The same snapshot, engine and settings as scripts/native7_rules_only.py; the only difference is that appending a non-finite equity value to
engine.daily_assets raises, so the engine state (cash, positions, bars, events) is the state that produced the first invalid value.
"""
import argparse
import contextlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import native7_rules_only as n7
from scripts.audit_strategy_history import read, write
from scripts.track_b_validation import OUT


class FirstNonFinite(Exception):
    pass


class Guard(list):
    def append(self, value):
        if not math.isfinite(value):
            raise FirstNonFinite(len(self))
        super().append(value)


def bar_row(data, sid, day):
    try:
        return {k: v for k, v in data.bar(sid, day).items() if k in ('open_price', 'high_price', 'low_price', 'close_price', 'volume', 'amount')}
    except KeyError:
        return 'NO_BAR_ROW'


def trace(start, end, key):
    from core.backtest.runner import BacktestEngine
    from core.research.native_frame import NativeResearchData, load_publication_dates
    from scripts.native_recovery import isolated

    snap = n7.build_snapshot(start, end)
    fin = pd.read_parquet(n7.MYSQL / 'snapshot_financial_statements.parquet')
    rev = pd.read_parquet(n7.MYSQL / 'snapshot_monthly_revenue.parquet')
    data = NativeResearchData(snap, fin[fin.year >= 1911], rev, publications=load_publication_dates())
    sessions = [d.strftime('%Y-%m-%d') for d in sorted(snap.trade_date.unique()) if start <= d.strftime('%Y-%m-%d') <= end]
    settings = read(n7.NATIVE7 / 'user_settings.json')
    use = [d for d in sessions if d <= (min(end, n7.FINANCIALS_PUBLISHED_UNTIL) if key == 'quality_value_low_volatility' else end)]
    delisted = pd.read_csv(OUT / 'delistings.csv', dtype=str) if (OUT / 'delistings.csv').exists() else pd.DataFrame()
    events = pd.read_csv(OUT / 'official_events.csv', dtype=str)
    out = {'strategy': key, 'window': [start, end], 'sessions': len(use), 'first_non_finite': None}
    with isolated(data, settings):
        with (OUT / f'native7_trace_{key}.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
            engine = BacktestEngine(mode=key, start_date=start, end_date=use[-1], initial_capital=1_000_000, persist_results=False, persist_to_db=False,
                                    execution_contract='next_session_open_v2', trading_sessions=use, research_data=data, rules_only=True)
            engine.daily_assets = Guard()
            try:
                engine.run()
            except FirstNonFinite as stop:
                index = stop.args[0]
            except AttributeError:
                index = None  # finished and crashed in calculate_metrics: the curve was non-finite after all, but the Guard did not fire
        if index is None:
            out['result'] = 'NO_NON_FINITE_DAILY_ASSET' if len(engine.daily_assets) == len(use) else 'INCOMPLETE'
            write(OUT / f'native7_nan_trace_{key}.json', n7.clean(out))
            return out
        day = use[index]
        previous = engine.daily_assets[-1] if engine.daily_assets else None
        components, bad = [], []
        for sid, info in engine.positions.items():
            row = bar_row(data, sid, day)
            close = None if row == 'NO_BAR_ROW' else row.get('close_price')
            finite = close is not None and math.isfinite(float(close))
            history = [{'date': str(d.date()), **{k: (None if pd.isna(v) else float(v)) for k, v in r.items()}} for d, r in
                       snap[(snap.stock_id == sid) & (snap.trade_date <= pd.Timestamp(day))].tail(6).set_index('trade_date')[['open_price', 'close_price', 'volume']].iterrows()]
            fills = [{k: e.get(k) for k in ('signal_date', 'execution_date', 'side', 'price', 'status', 'reason')} for e in engine.execution_events if e.get('stock_id') == sid]
            later = snap[(snap.stock_id == sid) & (snap.trade_date > pd.Timestamp(day))]
            item = {'stock_id': sid, 'shares': info.get('shares'), 'cost': info.get('cost'), 'engine_last_price': info.get('last_price'), 'bar_on_first_bad_day': row,
                    'close_is_finite': bool(finite), 'last_6_snapshot_bars': history, 'execution_events': fills,
                    'later_bars_in_snapshot': int(len(later)), 'later_bars_with_finite_close': int(later.close_price.notna().sum()),
                    'in_delistings_csv': bool((not delisted.empty) and (delisted.astype(str).eq(sid).any().any())),
                    'official_events_on_day': events[(events.stock_id.astype(str) == sid) & (events.iloc[:, 0].astype(str).str[:10] == day)].to_dict('records') if 'stock_id' in events else []}
            components.append(item)
            if not finite:
                bad.append(sid)
        finite_value = sum(i['shares'] * (i['bar_on_first_bad_day'].get('close_price') if i['close_is_finite'] else 0) for i in components)
        window = snap[(snap.trade_date >= pd.Timestamp(start)) & (snap.trade_date <= pd.Timestamp(end))]
        out.update({'first_non_finite': day, 'session_index': index, 'cash': engine.capital, 'previous_equity': previous, 'previous_date': use[index - 1] if index else None,
                    'finite_market_value': finite_value, 'positions_held': len(components), 'positions_with_non_finite_close': bad, 'positions': components,
                    'closed_trades_before': len(engine.trades),
                    'snapshot_rows_without_close_in_window': int(window.close_price.isna().sum()), 'snapshot_rows_in_window': int(len(window)),
                    'snapshot_rows_without_close_but_with_volume_in_window': int((window.close_price.isna() & (window.volume > 0)).sum()),
                    'result': 'FIRST_NON_FINITE_FOUND'})
    write(OUT / f'native7_nan_trace_{key}.json', n7.clean(out))
    return out


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--strategy', required=True)
    args = parser.parse_args()
    result = trace(args.start, args.end, args.strategy)
    print({k: result.get(k) for k in ('result', 'first_non_finite', 'positions_with_non_finite_close')})
