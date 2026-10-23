"""OD-29 valuation fix evidence: preserve the running state, profile the invalid-close rows, and replay short windows for characterization.

Stages:
  preserve      model / archive / Frozen hashes (dual_track_validation.baseline) + every Native7 run file and script version as of now
  profile       official rows without a regular-session close; the 2,041 vs 1,772 counts; the MySQL non-positive closes
  replay LABEL  short real-data replay (mean_reversion, defensive_low_volatility) with the engine as currently on disk; one JSON per label
  compare       legacy replay before vs after the change must be identical; stale policy must leave every order and fill unchanged
  results       the five OD-29 reruns: performance, stale valuation, zero-volume classes, difference to seeded legacy and pre-fix runs
"""
import json
import math
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import dual_track_validation as dtv
from scripts import native7_rules_only as n7
from scripts.audit_strategy_history import digest, read, write
from scripts.track_b_validation import OUT

OD29 = OUT / 'od29_valuation'
REPLAY = ('2023-04-03', '2023-06-30')  # contains the proven 2023-05-11 case (1225 held by mean_reversion)
REPLAY_STRATEGIES = ('mean_reversion', 'defensive_low_volatility')
MYSQL_BARS = n7.MYSQL / 'snapshot_daily_market_data.parquet'
SLIPPAGE_SEED = 20261023  # same seed as native7_rules_only.SLIPPAGE_SEED (OD-29 reruns)


def new(name):
    path = OD29 / name
    if path.exists():
        raise FileExistsError(path)  # evidence is never overwritten
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def preserve():
    dtv.baseline('od29_valuation/baseline.json')
    files = {}
    for path in sorted(OUT.glob('native7_*')):
        try:
            files[path.name] = {'bytes': path.stat().st_size, 'sha256': digest(path), 'modified': datetime.fromtimestamp(path.stat().st_mtime).isoformat()}
        except PermissionError:  # still open for writing by a running process
            files[path.name] = {'bytes': path.stat().st_size, 'sha256': None, 'note': 'locked by a running process', 'modified': datetime.fromtimestamp(path.stat().st_mtime).isoformat()}
    running = subprocess.run(['powershell', '-NoProfile', '-Command', "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'native7_' } | "
                              "ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId) $($_.CreationDate.ToString('s')) $($_.CommandLine)\" }"], capture_output=True, text=True).stdout.splitlines()
    write(new('preserved_runs.json'), {'at': datetime.now().isoformat(timespec='seconds'), 'git_head': dtv.git('rev-parse', 'HEAD').strip(),
                                       'running_processes': running, 'files': files,
                                       'scripts_sha256': {p: digest(ROOT / p) for p in ('scripts/native7_rules_only.py', 'scripts/native7_nan_trace.py', 'scripts/dual_track_matrix.py',
                                                                                         'core/backtest/runner.py', 'core/backtest/metrics.py')},
                                       'note': 'running processes loaded their code at start; files still being written are hashed again when they finish'})
    print('preserved', len(files), 'files;', len(running), 'processes')


def profile():
    bars = pd.read_parquet(OUT / 'official_bars.parquet')
    missing = bars[bars.close.isna()]
    count = lambda a, b: int(((missing.trade_date >= a) & (missing.trade_date <= b)).sum())
    lookback_start = str((pd.Timestamp(REPLAY[0]) - pd.Timedelta(days=0)).date())
    windows = {'all cached sessions': (str(bars.trade_date.min().date()), str(bars.trade_date.max().date())),
               'earlier scan (2022-08-01..2026-05-14)': ('2022-08-01', '2026-05-14'),
               'Native7 backtest window (2023-01-03..2026-05-14)': ('2023-01-03', '2026-05-14'),
               'Native7 snapshot incl. 160-day lookback': (str((pd.Timestamp('2023-01-03') - pd.Timedelta(days=n7.LOOKBACK_DAYS)).date()), '2026-05-14')}
    counts = {k: {'from': a, 'to': b, 'rows_without_close': count(a, b)} for k, (a, b) in windows.items()}
    in_window = missing[(missing.trade_date >= '2023-01-03') & (missing.trade_date <= '2026-05-14')]
    counts['split of the backtest window'] = {'with_volume': int((in_window.volume > 0).sum()), 'zero_volume': int((in_window.volume == 0).sum()),
                                              'stocks': int(in_window.stock_id.nunique()), 'sessions': int(in_window.trade_date.nunique())}
    counts['difference 2,041 - 1,772'] = {'rows': count('2022-08-01', '2023-01-02'), 'reason': '2022-08-01..2023-01-02 is in the earlier scan (lookback period) but not in the backtest window'}

    mysql = pd.read_parquet(MYSQL_BARS, columns=['stock_id', 'trade_date', 'open_price', 'close_price', 'volume'])
    mysql['trade_date'], mysql['stock_id'] = pd.to_datetime(mysql.trade_date), mysql.stock_id.astype(str)
    bad = mysql[mysql.close_price <= 0].merge(bars.rename(columns={'open': 'official_open', 'close': 'official_close', 'volume': 'official_volume'})
                                              [['stock_id', 'trade_date', 'official_open', 'official_close', 'official_volume']], on=['stock_id', 'trade_date'], how='left')
    bad['official_row'] = bad.official_volume.notna()
    bad['classification'] = np.where(~bad.official_row, 'MYSQL_ROW_NOT_IN_OFFICIAL_LISTED_BARS',
                                     np.where(bad.official_close.isna(), 'OFFICIAL_NO_REGULAR_SESSION_PRICE_STORED_AS_ZERO_IN_MYSQL', 'MYSQL_VALUE_ERROR_OFFICIAL_HAS_PRICE'))
    overlap = missing.merge(mysql, on=['stock_id', 'trade_date'])
    result = {'official_rows_without_close': counts,
              'source_check': 'MI_INDEX publishes "--" for open/high/low/close when the stock had no board-lot trade; volume can still be >0 from odd-lot trades',
              'mysql_snapshot': {'rows': int(len(mysql)), 'null_close': int(mysql.close_price.isna().sum()), 'non_positive_close': int(len(bad)),
                                 'non_positive_rows': json.loads(bad.assign(trade_date=bad.trade_date.dt.strftime('%Y-%m-%d')).to_json(orient='records')),
                                 'official_no_close_rows_present_in_mysql': int(len(overlap)),
                                 'their_mysql_close_values': sorted(set(overlap.close_price.round(4).tolist()))}}
    write(new('invalid_close_profile.json'), result)
    print(json.dumps({k: v for k, v in counts.items()}, ensure_ascii=False, indent=1))
    print(bad[['stock_id', 'trade_date', 'close_price', 'official_close', 'official_volume', 'classification']].to_string(index=False))


def replay(label):
    """Short real-data replay with the engine on disk; records everything needed to compare versions exactly."""
    from core.backtest.runner import BacktestEngine
    from core.research.native_frame import NativeResearchData, load_publication_dates
    from scripts.native_recovery import isolated
    import contextlib
    import inspect

    start, end = REPLAY
    policy = label.split('-')[-1] if label.split('-')[-1] in ('legacy', 'finite_positive_stale') else None
    snap = n7.build_snapshot(start, end)
    fin = pd.read_parquet(n7.MYSQL / 'snapshot_financial_statements.parquet')
    rev = pd.read_parquet(n7.MYSQL / 'snapshot_monthly_revenue.parquet')
    data = NativeResearchData(snap, fin[fin.year >= 1911], rev, publications=load_publication_dates())
    sessions = [d.strftime('%Y-%m-%d') for d in sorted(snap.trade_date.unique()) if start <= d.strftime('%Y-%m-%d') <= end]
    settings = read(n7.NATIVE7 / 'user_settings.json')
    supports_policy = 'valuation_policy' in inspect.signature(BacktestEngine.__init__).parameters
    out = {'label': label, 'slippage_seed': SLIPPAGE_SEED, 'window': [start, end], 'runner_sha256': digest(ROOT / 'core/backtest/runner.py'), 'valuation_policy': policy if supports_policy else 'pre-OD29 engine',
           'strategies': {}}
    with isolated(data, settings):
        for key in REPLAY_STRATEGIES:
            kwargs = {'valuation_policy': policy} if supports_policy and policy else {}
            random.seed(SLIPPAGE_SEED)  # slippage is drawn from the global random module; unseeded runs differ in every fill price
            with open(OD29 / f'replay_{label}_{key}.log', 'w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
                engine = BacktestEngine(mode=key, start_date=start, end_date=end, initial_capital=1_000_000, persist_results=False, persist_to_db=False,
                                        execution_contract='next_session_open_v2', trading_sessions=sessions, research_data=data, rules_only=True, **kwargs)
                error = None
                try:
                    engine.run()
                except Exception as exc:  # the legacy engine raises in calculate_metrics after the session loop when the curve is non-finite
                    error = f'{type(exc).__name__}: {exc}'
            out['strategies'][key] = {'error': error, 'daily_dates': engine.daily_dates, 'daily_assets': [repr(float(v)) for v in engine.daily_assets],
                                      'trades': json.loads(json.dumps(engine.trades, default=str)), 'execution_events': json.loads(json.dumps(engine.execution_events, default=str)),
                                      'valuation_events': json.loads(json.dumps(getattr(engine, 'valuation_events', []), default=str))}
    path = new(f'replay_{label}.json')
    path.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    print(label, {k: (v['error'], len(v['execution_events']), len(v['trades']), sum(x == 'nan' for x in v['daily_assets']), len(v['valuation_events'])) for k, v in out['strategies'].items()})


def compare():
    load = lambda label: json.loads((OD29 / f'replay_{label}.json').read_text(encoding='utf-8'))
    before, legacy, stale = load('before-seeded'), load('after-seeded-legacy'), load('after-seeded-finite_positive_stale')
    result = {'runner_sha256': {'before': before['runner_sha256'], 'after': legacy['runner_sha256']}, 'strategies': {}}
    for key in REPLAY_STRATEGIES:
        b, l, s = before['strategies'][key], legacy['strategies'][key], stale['strategies'][key]
        same_legacy = {f: b[f] == l[f] for f in ('error', 'daily_dates', 'daily_assets', 'trades', 'execution_events')}
        same_orders = {f: b[f] == s[f] for f in ('trades', 'execution_events', 'daily_dates')}
        diffs = [(d, x, y) for d, x, y in zip(b['daily_dates'], b['daily_assets'], s['daily_assets']) if x != y]
        result['strategies'][key] = {'legacy_identical_to_before': same_legacy, 'stale_orders_and_fills_identical': same_orders,
                                     'stale_equity_differs_on': len(diffs), 'first_differences': diffs[:5], 'before_nan_sessions': sum(x == 'nan' for x in b['daily_assets']),
                                     'stale_nan_sessions': sum(x == 'nan' for x in s['daily_assets']), 'stale_error': s['error'], 'stale_valuation_events': len(s['valuation_events']),
                                     'stale_events_sample': s['valuation_events'][:3]}
    result['LEGACY_CHARACTERIZATION'] = 'PASS' if all(all(v['legacy_identical_to_before'].values()) for v in result['strategies'].values()) else 'FAIL'
    result['ORDERS_UNCHANGED_BY_STALE_POLICY'] = 'PASS' if all(all(v['stale_orders_and_fills_identical'].values()) for v in result['strategies'].values()) else 'FAIL'
    write(new('replay_comparison.json'), result)
    print(result['LEGACY_CHARACTERIZATION'], result['ORDERS_UNCHANGED_BY_STALE_POLICY'], {k: (v['stale_equity_differs_on'], v['before_nan_sessions'], v['stale_nan_sessions']) for k, v in result['strategies'].items()})


STRATEGIES = ('defensive_low_volatility', 'mean_reversion', 'hybrid_trend_rank', 'institutional_flow_confirmation', 'quality_value_low_volatility')
PRE_FIX = {'defensive_low_volatility': 'native7_rules_only_prices.json', 'mean_reversion': 'native7_rules_only_prices2.json',
           'quality_value_low_volatility': 'native7_rules_only_prices2.json', 'hybrid_trend_rank': 'native7_rules_only_flows2.json',
           'institutional_flow_confirmation': 'native7_rules_only_flows2.json'}
SUSPENSION_LOOKBACK = 20  # sessions before a zero-volume day; a stock that traded on most of them and then shows no trade at all looks halted


def zero_volume_classes(events, bars):
    """NO_VALID_CLOSE_NO_VOLUME events: PROBABLE_SUSPENSION when the stock traded on at least 15 of the previous 20 sessions, else ILLIQUID_NO_TRADE.
    No official halt list is cached, so neither class is a verified suspension."""
    out = []
    for e in events[events.reason == 'NO_VALID_CLOSE_NO_VOLUME'].itertuples():
        prior = bars[(bars.stock_id == str(e.stock_id)) & (bars.trade_date < pd.Timestamp(e.date))].tail(SUSPENSION_LOOKBACK)
        traded = int((prior.volume > 0).sum())
        out.append({'date': e.date, 'stock_id': str(e.stock_id), 'equity_share': float(e.equity_share), 'prior_sessions_traded': traded,
                    'prior_median_volume': float(prior.volume.median()) if len(prior) else None,
                    'class': 'PROBABLE_SUSPENSION' if traded >= 15 else 'ILLIQUID_NO_TRADE'})
    return out


def results():
    bars = pd.read_parquet(OUT / 'official_bars.parquet', columns=['trade_date', 'stock_id', 'volume'])
    metric = lambda run, k: run['metrics'][k]['value'] if run else None
    rows, detail = [], {}
    for key in STRATEGIES:
        stale_path, legacy_path = OUT / 'native7_od29' / f'native7_rules_only_od29_{key}.json', OUT / 'native7_od29_legacy' / f'native7_rules_only_od29legacy_{key}.json'
        stale = read(stale_path)['strategies'].get(key) if stale_path.exists() else None
        legacy = read(legacy_path)['strategies'].get(key) if legacy_path.exists() else None
        pre = read(OUT / PRE_FIX[key])['strategies'].get(key)
        events_path = OUT / 'native7_od29' / f'valuation_events_od29_{key}_{key}.csv'
        events = pd.read_csv(events_path, dtype={'stock_id': str}) if events_path.exists() else pd.DataFrame(columns=['reason', 'stock_id', 'date', 'equity_share'])
        zero = zero_volume_classes(events, bars)
        s = (stale or {}).get('stale_valuation', {})
        flags = list(s.get('flags', [])) + (['PROBABLE_SUSPENSION'] if any(z['class'] == 'PROBABLE_SUSPENSION' for z in zero) else [])
        same_orders = bool(stale and legacy and stale['fills'] == legacy['fills'] and stale['closed_trades'] == legacy['closed_trades'] and stale['unfilled'] == legacy['unfilled'])
        row = {'strategy': key, 'completed': stale is not None and stale['metrics_status'] == 'COMPUTED',
               'BACKTEST': stale['BACKTEST'] if stale else 'BLOCKED', 'EVIDENCE': 'INSUFFICIENT',
               'fills': stale and stale['fills'], 'closed_trades': stale and stale['closed_trades'],
               'total_return': metric(stale, 'total_return'), 'sharpe': metric(stale, 'sharpe'), 'max_drawdown': metric(stale, 'max_drawdown'),
               'cagr': metric(stale, 'cagr'), 'win_rate': metric(stale, 'win_rate'),
               'stale_events': s.get('events', 0), 'longest_carry_sessions': s.get('longest_carry_sessions'), 'max_stale_equity_share': s.get('max_equity_share'),
               'stale_sessions_share': s.get('share_of_sessions'), 'flags': ';'.join(flags),
               'legacy_seeded_status': legacy and legacy['metrics_status'], 'legacy_seeded_non_finite_sessions': legacy and legacy['non_finite_sessions'],
               'legacy_seeded_total_return': metric(legacy, 'total_return'), 'legacy_seeded_sharpe': metric(legacy, 'sharpe'),
               'orders_identical_to_legacy': same_orders,
               'pre_fix_unseeded_status': pre and pre['BACKTEST'], 'pre_fix_unseeded_total_return': metric(pre, 'total_return'),
               'pre_fix_unseeded_sharpe': metric(pre, 'sharpe'), 'pre_fix_unseeded_fills': pre and pre['fills']}
        for name in ('total_return', 'sharpe'):
            row[f'delta_{name}_vs_legacy_seeded'] = (row[name] - row[f'legacy_seeded_{name}']) if row[name] is not None and row[f'legacy_seeded_{name}'] is not None else None
        rows.append(row)
        detail[key] = {'row': row, 'sensitivity': s.get('sensitivity'), 'by_reason': s.get('by_reason'), 'worst_runs': s.get('worst_runs'), 'zero_volume_events': zero,
                       'stale_source': stale_path.name if stale else None, 'run': read(stale_path).get('run') if stale else None,
                       'stale_file_sha256': digest(stale_path) if stale else None, 'legacy_file_sha256': digest(legacy_path) if legacy else None}
    pd.DataFrame(rows).to_csv(new('native7_od29_results.csv'), index=False)
    write(new('native7_od29_results.json'), json.loads(json.dumps(detail, default=str)))
    print(pd.DataFrame(rows)[['strategy', 'completed', 'BACKTEST', 'fills', 'total_return', 'sharpe', 'max_drawdown', 'stale_events', 'longest_carry_sessions',
                              'max_stale_equity_share', 'flags', 'legacy_seeded_status', 'orders_identical_to_legacy']].to_string(index=False))

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    stage = sys.argv[1]
    {'preserve': preserve, 'profile': profile, 'compare': compare, 'results': results}.get(stage, lambda: replay(sys.argv[2]))()
