"""Native-7 rules-only diagnostic backtests (OD-25, OD-28): strategy rules on the all-official snapshot, no model, next-session-open fills.

The snapshot is built only from official MI_INDEX bars, T86 and MI_MARGN (no MySQL row). Prices are raw (price return), so the benchmark is the
TAIEX price index and the total return is not available. Strategies that need monthly revenue are BLOCKED (no announcement time).
"""
import argparse
import contextlib
import hashlib
import json
import math
import random
from datetime import datetime
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
SLIPPAGE_SEED = 20261023  # BacktestEngine draws slippage from the global random module: seed it per strategy so a run is reproducible


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


METRICS = ('total_return', 'cagr', 'annualized_volatility', 'sharpe', 'max_drawdown', 'win_rate', 'trade_count')


def equity_performance(equity, trades, bench):
    """Performance from a daily equity curve. A non-finite equity value makes every metric undefined (reason recorded, never 0): the engine's
    calculate_metrics raises inside pstdev on NaN/inf (statistics works on exact fractions), and dropping those days would hide the data fault.
    """
    from core.backtest.metrics import calculate_metrics

    values = equity.to_numpy(dtype=float)
    bad = ~np.isfinite(values)
    if bad.any() or len(values) < 2:
        sample = ', '.join(str(d.date()) for d in equity.index[bad][:10])
        reason = (f'NON_FINITE_EQUITY: {int(bad.sum())} of {len(values)} sessions, first {equity.index[bad.argmax()].date()} (sample: {sample})' if bad.any() else 'fewer than 2 sessions')
        return {'metrics_status': 'UNDEFINED', 'metrics_method': 'none', 'non_finite_sessions': int(bad.sum()), 'metrics': {k: {'value': None, 'reason': reason} for k in METRICS}}
    engine = calculate_metrics(list(values), trades)  # same formulas as the engine (population std, 252 sessions, no risk-free rate)
    metrics = {k: {'value': engine.get(k).value, 'reason': engine.get(k).reason} for k in METRICS}
    r = equity.pct_change().dropna()
    excess = (r - bench.pct_change().reindex(r.index)).dropna()
    paired = bench.reindex(equity.index)
    return {'metrics_status': 'COMPUTED', 'metrics_method': 'core.backtest.metrics.calculate_metrics on a finite equity curve', 'non_finite_sessions': 0, 'metrics': metrics,
            'excess_sharpe_vs_price_index': st.sharpe(excess), 'excess_bootstrap': st.block_bootstrap_sharpe(excess),
            'benchmark_total_return': float(paired.iloc[-1] / paired.iloc[0] - 1) if paired.notna().all() else None}

STALE_MATERIAL = {'max_equity_share': 0.05, 'max_stale_days': 5}  # beyond either, a carried mark could move the result: flagged, never upgraded
DOWN_LIMIT = 0.10  # TWSE daily limit: the worst case for one session without a price


def stale_summary(events, equity, trades, bench, sessions):
    """STALE_VALUATION events -> counts, longest carry, largest share of equity, structural flags, and the equity curve under other carry assumptions.

    The alternative curves are exact: a mark never feeds position sizing (cash-based) or exits (raw close), so every order and fill is unchanged
    (tested in test_valuation_policy) and only that day's position value moves.
    """
    if not events:
        return {'events': 0, 'flags': [], 'sensitivity': None}
    e = pd.DataFrame(events)
    runs = e.groupby(['stock_id', 'price_source_date']).agg(start=('date', 'min'), end=('date', 'max'), sessions=('stale_days', 'max'),
                                                             reasons=('reason', lambda r: sorted(set(r))), max_share=('equity_share', 'max')).reset_index()
    ex = pd.read_csv(OUT / 'official_events.csv', dtype={'stock_id': str})
    gone = pd.read_csv(OUT / 'delistings.csv', dtype={'stock_id': str}, encoding='utf-8-sig').set_index('stock_id')
    runs['ex_date_in_carry'] = [bool(((ex.stock_id == r.stock_id) & (ex.ex_date > r.price_source_date) & (ex.ex_date <= r.end)).any()) for r in runs.itertuples()]
    runs['delisted_after_carry'] = [bool(r.stock_id in gone.index and str(gone.loc[r.stock_id, 'last_traded']) <= r.end) for r in runs.itertuples()]
    runs['possible_suspension'] = [bool(r.sessions >= STALE_MATERIAL['max_stale_days'] and r.reasons == ['NO_BAR_ROW']) for r in runs.itertuples()]
    flags = []
    if runs.delisted_after_carry.any():
        flags.append('DELISTING_EXIT_UNVERIFIED')
    if runs.ex_date_in_carry.any():
        flags.append('EX_RIGHTS_DURING_CARRY')
    if runs.possible_suspension.any():
        flags.append('POSSIBLE_SUSPENSION')
    if e.equity_share.max() > STALE_MATERIAL['max_equity_share'] or runs.sessions.max() > STALE_MATERIAL['max_stale_days']:
        flags.append('STALE_VALUATION_MATERIAL')
    adjust = lambda f: equity.add(e.assign(d=pd.to_datetime(e.date), a=f(e)).groupby('d').a.sum(), fill_value=0.0)
    scenarios = {'carry_last_valid_close': equity,
                 'down_limit_each_stale_session': adjust(lambda x: x.value * ((1 - DOWN_LIMIT) ** x.stale_days - 1)),
                 'stale_positions_at_zero': adjust(lambda x: -x.value)}
    sensitivity = {}
    for name, curve in scenarios.items():
        m = equity_performance(curve, trades, bench)
        sensitivity[name] = {k: m['metrics'][k]['value'] for k in ('total_return', 'sharpe', 'max_drawdown')}
    return {'events': len(e), 'stocks': int(e.stock_id.nunique()), 'sessions_with_stale_marks': int(e.date.nunique()), 'share_of_sessions': e.date.nunique() / len(sessions),
            'by_reason': e.reason.value_counts().to_dict(), 'longest_carry_sessions': int(runs.sessions.max()), 'max_equity_share': float(e.equity_share.max()),
            'mean_equity_share_when_stale': float(e.groupby('date').equity_share.sum().mean()), 'carry_runs': int(len(runs)),
            'runs_with_ex_date': int(runs.ex_date_in_carry.sum()), 'runs_ending_in_delisting': int(runs.delisted_after_carry.sum()),
            'runs_possible_suspension': int(runs.possible_suspension.sum()), 'flags': flags, 'sensitivity': sensitivity,
            'worst_runs': json.loads(runs.sort_values(['max_share', 'sessions'], ascending=False).head(10).to_json(orient='records'))}


def provenance(tag):
    """Run id, script version and input identities, so the matrix can tell results apart."""
    sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {'run_id': f"{tag}-{datetime.now().strftime('%Y%m%dT%H%M%S')}", 'script_sha256': sha(__file__),
            'inputs_sha256': {p.name: sha(p) for p in (OUT / 'official_bars.parquet', OUT / 'benchmarks.csv', NATIVE7 / 'user_settings.json')}}


def run(start, end, strategies, tag, policy='legacy', out_dir=OUT):
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
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {'run': {**provenance(tag), 'valuation_policy': policy, 'slippage_seed': SLIPPAGE_SEED}, 'window': [start, end], 'sessions': len(sessions), 'basis': 'raw prices (price return); benchmark TAIEX price index', 'strategies': {}}
    with isolated(data, settings):
        for key in strategies:
            end_key = min(end, FINANCIALS_PUBLISHED_UNTIL) if key == 'quality_value_low_volatility' else end
            use = [d for d in sessions if d <= end_key]
            engine_error = None
            with (out_dir / f'native7_{tag}_{key}.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
                engine = BacktestEngine(mode=key, start_date=start, end_date=end_key, initial_capital=1_000_000, persist_results=False, persist_to_db=False,
                                        execution_contract='next_session_open_v2', trading_sessions=use, research_data=data, rules_only=True,
                                        **({'valuation_policy': policy} if policy != 'legacy' else {}))
                random.seed(SLIPPAGE_SEED)
                try:
                    engine.run()
                except AttributeError as error:
                    # engine.run() always ends with calculate_metrics (runner.py:1299), which raises in pstdev when the equity curve holds NaN/inf
                    # (core/backtest/metrics.py:55, production-live, left unchanged). The session loop has finished by then; anything else is re-raised.
                    if 'numerator' not in str(error) or len(engine.daily_assets) != len(use):
                        raise
                    engine_error = f'{type(error).__name__}: {error}'
                except ValueError as error:  # finite_positive_stale: a position with no valid close since entry stops the run (never valued at 0)
                    if 'VALUATION_BLOCKED' not in str(error):
                        raise
                    engine_error = f'{type(error).__name__}: {error}'
            equity = pd.Series(engine.daily_assets, index=pd.to_datetime(engine.daily_dates), dtype=float)
            fills = [e for e in engine.execution_events if e.get('status') == 'FILLED']
            blocked_valuation = engine_error is not None and 'VALUATION_BLOCKED' in engine_error
            performance = equity_performance(equity, engine.trades, bench) if not blocked_valuation else {
                'metrics_status': 'UNDEFINED', 'metrics_method': 'none', 'non_finite_sessions': 0, 'metrics': {k: {'value': None, 'reason': engine_error} for k in METRICS}}
            events = getattr(engine, 'valuation_events', [])
            stale = stale_summary(events, equity, engine.trades, bench, use) if performance['metrics_status'] == 'COMPUTED' else {'events': len(events), 'flags': [], 'sensitivity': None}
            if events:
                pd.DataFrame(events).to_csv(out_dir / f'valuation_events_{tag}_{key}.csv', index=False)
            backtest = 'BLOCKED' if performance['metrics_status'] != 'COMPUTED' or 'DELISTING_EXIT_UNVERIFIED' in stale['flags'] else 'DIAGNOSTIC'
            result['strategies'][key] = {
                'window': [start, end_key], 'sessions': len(use), 'required_inputs': engine.required_inputs, 'fills': len(fills), 'closed_trades': len(engine.trades),
                'unfilled': {k: sum(e.get('status') == k for e in engine.execution_events) for k in {e.get('status') for e in engine.execution_events} if k != 'FILLED'},
                'final_equity': engine.daily_assets[-1] if engine.daily_assets else None, **performance, 'engine_run_error': engine_error,
                'data_exclusions_total': sum(x['excluded'] for x in engine.data_exclusions), 'valuation_policy': policy, 'stale_valuation': stale,
                'BACKTEST': backtest, 'EVIDENCE': 'INSUFFICIENT',
                'reasons': ['rule parameters were tuned on earlier data (trial count unknown), so no selection-adjusted statistic is possible',
                            'raw-price basis: dividends are not included (total return BLOCKED)', 'execution audit not applied to engine events in this stage',
                            'no model: the rules were tested alone'] + [f'stale valuation: {f}' for f in stale['flags']]}
            write(out_dir / f'native7_rules_only_{tag}.json', clean(result))  # incremental: a later crash must not lose finished strategies
    result['blocked'] = BLOCKED
    write(out_dir / f'native7_rules_only_{tag}.json', clean(result))
    print({k: (v['fills'], v['closed_trades'], v['metrics_status'], v['metrics']['total_return']['value']) for k, v in result['strategies'].items()})


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--strategies', default=','.join(RUNNABLE))
    parser.add_argument('--valuation-policy', default='legacy', choices=('legacy', 'finite_positive_stale'))
    parser.add_argument('--out-dir', default=str(OUT))
    args = parser.parse_args()
    run(args.start, args.end, args.strategies.split(','), args.tag, args.valuation_policy, Path(args.out_dir))
