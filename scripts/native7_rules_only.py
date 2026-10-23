"""Native-7 rules-only diagnostic backtests (OD-25, OD-28): strategy rules on the all-official snapshot, no model, next-session-open fills.

The snapshot is built only from official MI_INDEX bars, T86 and MI_MARGN (no MySQL row). Prices are raw (price return), so the benchmark is the
TAIEX price index and the total return is not available. Strategies that need monthly revenue are BLOCKED (no announcement time).
"""
import argparse
import contextlib
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.official_data_recovery as odr
from scripts import track_b_stats as st
from scripts.audit_strategy_history import read, write
from scripts.track_b_validation import OUT

NATIVE7 = ROOT / 'outputs/native7_recovery/2026-10-22'
MYSQL = ROOT / 'outputs/strategy_validation/2026-10-21'
RUNNABLE = ('defensive_low_volatility', 'hybrid_trend_rank', 'institutional_flow_confirmation', 'mean_reversion', 'quality_value_low_volatility')
BLOCKED = {'growth_momentum_breakout': 'rule uses revenue_yoy and monthly revenue has no official announcement time',
           'quality_growth': 'rule uses revenue_yoy and monthly revenue has no official announcement time'}
FINANCIALS_PUBLISHED_UNTIL = '2026-05-14'  # filings with an actual MOPS publication date exist through FY2025; FY2026Q1 is only a deadline estimate after 05-15
LOOKBACK_DAYS = 160


def build_snapshot(start, end):
    """Official bars + T86 + MI_MARGN for every session from (start - lookback) to end."""
    bars = pd.read_parquet(OUT / 'official_bars.parquet')
    first = pd.Timestamp(start) - pd.Timedelta(days=LOOKBACK_DAYS)
    bars = bars[(bars.trade_date >= first) & (bars.trade_date <= pd.Timestamp(end))]
    days = sorted(d.strftime('%Y-%m-%d') for d in bars.trade_date.unique())
    chips = odr.official_chips(days, ('T86', 'MI_MARGN'), read(odr.PREV_CHIPS)['files'])
    snap = bars.rename(columns={'open': 'open_price', 'high': 'high_price', 'low': 'low_price', 'close': 'close_price'}).merge(chips, on=odr.KEYS, how='left')
    snap['short_balance'] = np.nan
    return snap


def clean(value):
    """NaN / inf -> None so the evidence is strict JSON (an undefined statistic is reported as null, never as 0)."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(value) else float(value)
    return value


def run(start, end, strategies, tag):
    from core.backtest.metrics import calculate_metrics
    from core.backtest.runner import BacktestEngine
    from core.research.native_frame import NativeResearchData, load_publication_dates
    from scripts.native_recovery import isolated

    snap = build_snapshot(start, end)
    fin = pd.read_parquet(MYSQL / 'snapshot_financial_statements.parquet')
    rev = pd.read_parquet(MYSQL / 'snapshot_monthly_revenue.parquet')
    data = NativeResearchData(snap, fin[fin.year >= 1911], rev, publications=load_publication_dates())
    sessions = [d.strftime('%Y-%m-%d') for d in sorted(snap.trade_date.unique()) if start <= d.strftime('%Y-%m-%d') <= end]
    settings = read(NATIVE7 / 'user_settings.json')
    bench = pd.read_csv(OUT / 'benchmarks.csv', parse_dates=['date']).set_index('date').taiex_price
    result = {'window': [start, end], 'sessions': len(sessions), 'basis': 'raw prices (price return); benchmark TAIEX price index', 'strategies': {}}
    with isolated(data, settings):
        for key in strategies:
            end_key = min(end, FINANCIALS_PUBLISHED_UNTIL) if key == 'quality_value_low_volatility' else end
            use = [d for d in sessions if d <= end_key]
            with (OUT / f'native7_{tag}_{key}.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
                engine = BacktestEngine(mode=key, start_date=start, end_date=end_key, initial_capital=1_000_000, persist_results=False, persist_to_db=False,
                                        execution_contract='next_session_open_v2', trading_sessions=use, research_data=data, rules_only=True)
                engine.run(return_metrics=True)
            equity = pd.Series(engine.daily_assets, index=pd.to_datetime(engine.daily_dates), dtype=float)
            r = equity.pct_change().dropna()
            excess = (r - bench.pct_change().reindex(r.index)).dropna()
            metrics = calculate_metrics(engine.daily_assets, engine.trades)
            fills = [e for e in engine.execution_events if e.get('status') == 'FILLED']
            result['strategies'][key] = {
                'window': [start, end_key], 'sessions': len(use), 'required_inputs': engine.required_inputs, 'fills': len(fills), 'closed_trades': len(engine.trades),
                'unfilled': {k: sum(e.get('status') == k for e in engine.execution_events) for k in {e.get('status') for e in engine.execution_events} if k != 'FILLED'},
                'final_equity': engine.daily_assets[-1] if engine.daily_assets else None, 'total_return': float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) else None,
                'sharpe': st.sharpe(r), 'max_drawdown': st.max_drawdown(r) if len(r) else None, 'excess_sharpe_vs_price_index': st.sharpe(excess),
                'excess_bootstrap': st.block_bootstrap_sharpe(excess), 'benchmark_total_return': float(bench.reindex(equity.index).iloc[-1] / bench.reindex(equity.index).iloc[0] - 1) if len(equity) else None,
                'engine_metrics': {k: {'value': (m.value if m.value is None or math.isfinite(m.value) else None), 'reason': m.reason} for k in ('cagr', 'win_rate') for m in [metrics.get(k)]},
                'data_exclusions_total': sum(x['excluded'] for x in engine.data_exclusions),
                'BACKTEST': 'DIAGNOSTIC', 'EVIDENCE': 'INSUFFICIENT',
                'reasons': ['rule parameters were tuned on earlier data (trial count unknown), so no selection-adjusted statistic is possible',
                            'raw-price basis: dividends are not included (total return BLOCKED)', 'execution audit not applied to engine events in this stage',
                            'no model: the rules were tested alone']}
    result['blocked'] = BLOCKED
    write(OUT / f'native7_rules_only_{tag}.json', clean(result))
    print({k: (v['fills'], v['closed_trades'], None if v['total_return'] is None else round(v['total_return'], 4)) for k, v in result['strategies'].items()})


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--strategies', default=','.join(RUNNABLE))
    args = parser.parse_args()
    run(args.start, args.end, args.strategies.split(','), args.tag)
