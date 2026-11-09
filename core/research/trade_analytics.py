"""Pure research analytics for lab runs: performance, MAE/MFE, liquidity, factors, avg_score.

Every metric is ``{'value': float | None, 'reason': str | None}``; undefined values are
``None`` with a reason, never 0 or infinity. No file IO, no backtests.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from core.backtest.metrics import calculate_metrics, max_drawdown
from core.research.factor_evaluation import (
    EvaluationPolicy, compute_annual_results, compute_daily_rank_ic, summarize_ic, summarize_quantiles,
)

TRADING_DAYS = 252
METRIC_DIRECTIONS = {
    'cagr': +1, 'total_return': +1, 'sharpe': +1, 'win_rate': +1, 'profit_factor': +1, 'alpha': +1,
    'capacity': +1, 'max_drawdown_abs': -1, 'cost_ratio': -1, 'turnover_annual': -1, 'yearly_return_std': -1,
}
SCORE_NOTE = 'EXPLORATORY_RELATIVE_SCORE_NOT_CERTIFICATION_OR_WIN_PROBABILITY'


def _m(value=None, reason='NOT_COMPUTABLE') -> dict:
    if value is not None:
        value = float(value)
        if math.isfinite(value):
            return {'value': value, 'reason': None}
        if not math.isnan(value):
            reason = 'NON_FINITE'
    return {'value': None, 'reason': reason}


def _day(value) -> str:
    return pd.Timestamp(value).strftime('%Y-%m-%d')


def _series(values) -> pd.Series:
    series = pd.Series(values, dtype=float)
    series.index = [_day(item) for item in series.index]
    return series.groupby(level=0).last().sort_index()


def _trades(trades) -> pd.DataFrame:
    frame = pd.DataFrame(trades if trades is not None else []).copy()
    if frame.empty:
        return frame
    for column in ('buy_date', 'sell_date'):
        frame[column] = frame[column].map(_day)
    computed = (frame['sell_price'] - frame['buy_price']) * frame['shares'] - frame.get('cost', 0.0)
    frame['pnl'] = frame['pnl'].fillna(computed) if 'pnl' in frame else computed
    return frame


def performance(nav, *, initial_cash, benchmark=None, trades=None, costs=None, turnover_notional=None) -> dict:
    """Run-level metrics; equity is ``[initial_cash] + nav`` exactly as repair_runner feeds calculate_metrics."""
    nav = _series(nav)
    equity = [float(initial_cash)] + nav.tolist()
    base = calculate_metrics(equity).values
    out = {name: _m(base[name].value, base[name].reason) for name in ('total_return', 'cagr', 'sharpe', 'max_drawdown')}
    mdd, _, recovery = max_drawdown(equity)
    out['recovery_days'] = (_m(None, mdd.reason) if mdd.value is None
                            else _m(None, 'NOT_RECOVERED') if recovery is None else _m(recovery))

    yearly, previous = {}, float(initial_cash)
    for year, end in pd.Series(nav.values, index=pd.to_datetime(nav.index)).groupby(lambda d: d.year).last().items():
        yearly[int(year)] = _m(end / previous - 1 if previous > 0 else None, 'NON_POSITIVE_BASE')['value']
        previous = end
    out['yearly_returns'] = {'value': yearly, 'reason': None} if yearly else _m(None, 'EMPTY_NAV')
    finite = [value for value in yearly.values() if value is not None]
    out['yearly_return_std'] = _m(np.std(finite, ddof=1)) if len(finite) > 1 else _m(None, 'FEWER_THAN_TWO_YEARS')

    frame = _trades(trades)
    if frame.empty:
        for name in ('win_rate', 'avg_win', 'avg_loss', 'profit_factor', 'top5_profit_share'):
            out[name] = _m(None, 'NO_TRADES')
        out['trade_count'] = _m(0) if trades is not None else _m(None, 'TRADES_UNAVAILABLE')
    else:
        pnl = frame['pnl'].astype(float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        out['trade_count'] = _m(len(pnl))
        out['win_rate'] = _m(len(wins) / len(pnl))
        out['avg_win'] = _m(wins.mean()) if len(wins) else _m(None, 'NO_WINNING_TRADES')
        out['avg_loss'] = _m(losses.mean()) if len(losses) else _m(None, 'NO_LOSING_TRADES')
        out['profit_factor'] = _m(wins.sum() / -losses.sum()) if len(losses) else _m(None, 'NO_LOSING_TRADES')
        by_stock = pnl.groupby(frame['stock_id']).sum()
        positive = by_stock[by_stock > 0].sort_values(ascending=False)
        out['top5_profit_share'] = _m(positive.head(5).sum() / positive.sum()) if len(positive) else _m(None, 'NO_POSITIVE_PROFIT')

    if turnover_notional is None and not frame.empty:
        turnover_notional = float(((frame['buy_price'] + frame['sell_price']) * frame['shares']).sum())
    if turnover_notional is None:
        out['turnover_annual'] = _m(None, 'TURNOVER_UNAVAILABLE')
    elif nav.empty or nav.mean() <= 0:
        out['turnover_annual'] = _m(None, 'EMPTY_NAV')
    else:
        out['turnover_annual'] = _m(turnover_notional / nav.mean() / (len(nav) / TRADING_DAYS))

    if costs is not None:
        cost_total = float(np.sum(costs))
    elif not frame.empty and 'cost' in frame:
        cost_total = float(frame['cost'].fillna(0.0).sum())
    else:
        cost_total = None
    out['cost_total'] = _m(cost_total, 'COSTS_UNAVAILABLE')
    out['cost_ratio'] = _m(cost_total / initial_cash if cost_total is not None and initial_cash > 0 else None, 'COSTS_UNAVAILABLE')

    out.update({name: _m(None, 'BENCHMARK_UNAVAILABLE') for name in ('excess_return', 'alpha', 'beta')})
    if benchmark is not None:
        bench = _series(benchmark).reindex(nav.index)
        common = bench.dropna()
        if len(common) > 1 and out['total_return']['value'] is not None:
            out['excess_return'] = _m(out['total_return']['value'] - (common.iloc[-1] / common.iloc[0] - 1))
        pair = pd.DataFrame({'s': nav, 'b': bench}).pct_change(fill_method=None).dropna()
        if len(pair) < 30:
            out['alpha'] = out['beta'] = _m(None, 'FEWER_THAN_30_OVERLAPPING_DAYS')
        elif pair['b'].var(ddof=1) == 0:
            out['alpha'] = out['beta'] = _m(None, 'BENCHMARK_VARIANCE_ZERO')
        else:
            beta = pair['s'].cov(pair['b']) / pair['b'].var(ddof=1)
            out['beta'] = _m(beta)
            out['alpha'] = _m((pair['s'].mean() - beta * pair['b'].mean()) * TRADING_DAYS)

    daily = pd.Series(np.diff(equity), dtype=float)
    gains = daily[daily > 0].sort_values(ascending=False)
    best = max(1, math.ceil(0.05 * len(daily)))
    out['best5pct_day_share'] = _m(gains.head(best).sum() / gains.sum()) if len(gains) else _m(None, 'NO_POSITIVE_DAYS')

    def label(name, test):
        value = out[name]['value']
        return None if value is None else bool(test(value))
    # Observation labels only; never a gate or a score.
    out['observation_labels'] = {
        'sharpe_gt_1_5': label('sharpe', lambda v: v > 1.5),
        'win_rate_gt_50': label('win_rate', lambda v: v > 0.5),
        'mdd_ge_minus_30': label('max_drawdown', lambda v: v >= -0.30),
    }
    return out


def _bar_source(bars, calendar):
    """Return (sorted session days, lookup(day, stock_id) -> bar dict or None)."""
    if callable(bars):
        if calendar is None:
            raise ValueError('CALENDAR_REQUIRED_FOR_CALLABLE_BARS')
        cache: dict = {}

        def rows(day):
            if day not in cache:
                cache[day] = bars(day) or {}
            return cache[day]
        days = sorted({_day(item) for item in calendar})
    else:
        table = {_day(key): value or {} for key, value in bars.items()}
        days = sorted(table if calendar is None else {_day(item) for item in calendar})

        def rows(day):
            return table.get(day, {})

    def lookup(day, stock_id):
        row = rows(day)
        return row.get(stock_id, row.get(str(stock_id)))
    return days, lookup


def _held(days, start, end):
    return [day for day in days if start <= day <= end]


def mae_mfe(trades, bars, *, calendar=None) -> pd.DataFrame:
    """Per-trade MAE/MFE vs buy price over buy..sell inclusive; any missing bar -> NaN plus reason."""
    days, bar = _bar_source(bars, calendar)
    rows = []
    for trade in _trades(trades).to_dict('records'):
        held = _held(days, trade['buy_date'], trade['sell_date'])
        found = [bar(day, trade['stock_id']) for day in held]
        basis = trade['buy_price'] * trade['shares']
        row = {'stock_id': trade['stock_id'], 'buy_date': trade['buy_date'], 'sell_date': trade['sell_date'],
               'holding_days': len(held), 'return': trade['pnl'] / basis if basis else float('nan'),
               'outcome': 'WIN' if trade['pnl'] > 0 else 'LOSS', 'mae': float('nan'), 'mfe': float('nan'), 'reason': None}
        if not held or any(item is None for item in found):
            row['reason'] = 'MISSING_BARS'
        else:
            row['mae'] = min(float(item['low']) for item in found) / trade['buy_price'] - 1
            row['mfe'] = max(float(item['high']) for item in found) / trade['buy_price'] - 1
        rows.append(row)
    return pd.DataFrame(rows, columns=['stock_id', 'buy_date', 'sell_date', 'holding_days', 'return', 'outcome', 'mae', 'mfe', 'reason'])


def liquidity(trades, bars, *, limit_pct=0.095, calendar=None) -> dict:
    """Volume and tradability diagnostics; ``shares`` and bar ``volume`` must share the same unit."""
    days, bar = _bar_source(bars, calendar)
    position = {day: index for index, day in enumerate(days)}
    frame = _trades(trades)
    volumes, shares = [], []
    limit_days, suspended = set(), set()
    for trade in frame.to_dict('records'):
        stock = trade['stock_id']
        for day in {trade['buy_date'], trade['sell_date']}:
            item = bar(day, stock)
            if item and item.get('volume'):
                volumes.append(float(item['volume']))
                shares.append(float(trade['shares']) / float(item['volume']))
        for day in _held(days, trade['buy_date'], trade['sell_date']):
            item = bar(day, stock)
            if item is None:
                suspended.add((stock, day))
                continue
            previous = bar(days[position[day] - 1], stock) if position[day] else None
            if previous and previous.get('close') and abs(float(item['close']) / float(previous['close']) - 1) >= limit_pct:
                limit_days.add((stock, day))
    no_volume = 'NO_TRADES' if frame.empty else 'NO_VOLUME_DATA'
    unavailable = {'value': None, 'reason': 'NO_LOCAL_SOURCE'}
    return {
        'avg_volume': _m(np.mean(volumes) if volumes else None, no_volume),
        'min_volume': _m(min(volumes) if volumes else None, no_volume),
        'order_share_mean': _m(np.mean(shares) if shares else None, no_volume),
        'order_share_max': _m(max(shares) if shares else None, no_volume),
        'limit_days': _m(None, 'NO_TRADES') if frame.empty else _m(len(limit_days)),
        'suspension_days': _m(None, 'NO_TRADES') if frame.empty else _m(len(suspended)),
        'attention_flag': dict(unavailable), 'disposition_flag': dict(unavailable), 'full_delivery_flag': dict(unavailable),
    }


def factor_analysis(factor_panel, forward_returns, membership=None, *, horizon_end='2025-12-31',
                    min_assets=30, quantiles=5) -> dict:
    """Rank IC / ICIR / yearly IC / Q5-Q1 / coverage / correlation via factor_evaluation.

    factor_panel: rows (asof_date, asset_id, factor_id, value).
    forward_returns: rows (asof_date, asset_id, horizon, window_end, forward_return); windows ending
    after ``horizon_end`` are dropped. membership: optional rows (asof_date, asset_id); default is
    every asset present in factor_panel on that date.
    """
    end = _day(horizon_end)
    panel, forward = pd.DataFrame(factor_panel).copy(), pd.DataFrame(forward_returns).copy()
    for frame in (panel, forward):
        frame['asof_date'] = frame['asof_date'].map(_day)
        frame['asset_id'] = frame['asset_id'].astype(str)
    forward['window_end'] = forward['window_end'].map(_day)
    dropped = int((forward['window_end'] > end).sum())
    forward = forward[forward['window_end'] <= end]
    panel = panel[panel['asof_date'] <= end]
    result = {'horizon_end': end, 'dropped_windows': dropped, 'factors': {},
              'correlation': _m(None, 'NOT_COMPUTABLE: fewer than two factors')}
    if panel.empty or forward.empty:
        return {**result, 'status': 'NOT_COMPUTABLE', 'reason': 'INSUFFICIENT_DATA'}

    if membership is not None:
        members = pd.DataFrame(membership)[['asof_date', 'asset_id']].copy()
        members['asof_date'] = members['asof_date'].map(_day)
        members['asset_id'] = members['asset_id'].astype(str)
        members = members[members['asof_date'] <= end]
    else:
        members = panel[['asof_date', 'asset_id']]
    members = members.drop_duplicates().assign(member=True)
    factors = sorted(panel['factor_id'].unique())
    returns = forward.pivot_table(index=['asof_date', 'asset_id'], columns='horizon', values='forward_return', aggfunc='last')
    horizons = [int(h) for h in returns.columns]
    returns.columns = [f'forward_return_{h}d' for h in horizons]
    data = (members.merge(pd.DataFrame({'factor_id': factors}), how='cross')
            .merge(panel[['asof_date', 'asset_id', 'factor_id', 'value']], how='left', on=['asof_date', 'asset_id', 'factor_id'])
            .merge(returns.reset_index(), how='left', on=['asof_date', 'asset_id']))
    data['rank_value'] = data.groupby(['factor_id', 'asof_date'])['value'].rank(pct=True)
    data['direction'] = 1
    data['direction_adjusted_rank'] = data['rank_value']

    daily = compute_daily_rank_ic(data, horizons=horizons, policy=EvaluationPolicy(min_ic_assets=min_assets))
    summary = summarize_ic(daily)
    annual = compute_annual_results(daily)
    quantile_rows = []
    for (factor_id, _), group in data.groupby(['factor_id', 'asof_date']):
        for horizon in horizons:
            label = f'forward_return_{horizon}d'
            eligible = group.loc[group['rank_value'].notna() & group[label].notna()]
            if len(eligible) < max(min_assets, quantiles):
                continue
            bucket = np.clip(np.ceil(eligible['rank_value'] * quantiles), 1, quantiles)
            q1, q5 = eligible.loc[bucket == 1, label].mean(), eligible.loc[bucket == quantiles, label].mean()
            quantile_rows.append({'factor_id': factor_id, 'horizon': horizon, 'direction': 1,
                                  'raw_q1_return': q1, 'raw_q5_return': q5, 'raw_q5_minus_q1': q5 - q1})
    spreads = summarize_quantiles(pd.DataFrame(quantile_rows))
    coverage = data.groupby(['factor_id', 'asof_date'])['value'].apply(lambda s: s.notna().mean()).groupby(level=0).mean()

    short = f'NOT_COMPUTABLE: fewer than {min_assets} members with factor value and forward return'
    for row in summary.to_dict('records'):
        factor_id, horizon = row['factor_id'], int(row['horizon'])
        spread = spreads.loc[(spreads['factor_id'] == factor_id) & (spreads['horizon'] == horizon), 'raw_q5_minus_q1'] if not spreads.empty else pd.Series(dtype=float)
        years = annual.loc[(annual['factor_id'] == factor_id) & (annual['horizon'] == horizon)]
        result['factors'].setdefault(factor_id, {'coverage': _m(coverage.get(factor_id), 'NOT_COMPUTABLE: no members')})[horizon] = {
            'mean_ic': _m(row['raw_mean_ic'], short),
            'icir': _m(row['raw_icir'], 'NOT_COMPUTABLE: fewer than two valid IC days or zero IC std'),
            'valid_ic_days': int(row['raw_valid_ic_days']),
            'factor_return': _m(spread.iloc[0] if len(spread) else None, short),
            'yearly_ic': {int(y['year']): _m(y['aligned_mean_ic'], short) for y in years.to_dict('records')},
        }

    if len(factors) > 1:
        valued = data.loc[data['value'].notna()]
        matrices = [group.pivot(index='asset_id', columns='factor_id', values='value').corr(method='spearman', min_periods=3)
                    for _, group in valued.groupby('asof_date')]
        average = pd.concat(matrices).groupby(level=0).mean() if matrices else pd.DataFrame()
        result['correlation'] = {'value': {a: {b: _m(average.loc[a, b])['value'] if a in average.index and b in average.columns else None
                                               for b in factors} for a in factors}, 'reason': None}
    computable = any(entry[h]['mean_ic']['value'] is not None for entry in result['factors'].values() for h in entry if h != 'coverage')
    return {**result, 'status': 'OK' if computable else 'NOT_COMPUTABLE', 'reason': None if computable else 'INSUFFICIENT_DATA'}


def _raw_metrics(metrics) -> dict:
    out = {}
    for name, value in (metrics or {}).items():
        if isinstance(value, Mapping):
            value = value.get('value')
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[name] = float(value)
    if 'max_drawdown' in out and 'max_drawdown_abs' not in out:
        out['max_drawdown_abs'] = abs(out['max_drawdown'])
    return out


def avg_scores(runs: list[dict]) -> list[dict]:
    """Mean per-metric percentile among SUCCESS runs of one comparison group (exploratory only)."""
    success = [run for run in runs if run.get('status') == 'SUCCESS']
    if len({run.get('comparison_group') for run in success}) > 1:
        raise ValueError('MIXED_COMPARISON_GROUPS')
    raws = {run['run_id']: _raw_metrics(run.get('metrics')) for run in runs}
    percentiles: dict = {run['run_id']: {} for run in success}
    for metric, direction in METRIC_DIRECTIONS.items():
        values = pd.Series({rid: raws[rid][metric] for rid in percentiles if metric in raws[rid]}, dtype=float)
        # Lower-is-better metrics rank the negated value so both directions share one scale (best = 1.0).
        for rid, pct in (values * direction).rank(pct=True, method='average').items():
            percentiles[rid][metric] = float(pct)
    total = len(METRIC_DIRECTIONS)
    results = []
    for run in runs:
        rid = run['run_id']
        scored = percentiles.get(rid, {})
        item = {'run_id': rid, 'comparison_group': run.get('comparison_group'), 'status': run.get('status'),
                'raw': raws[rid], 'percentiles': scored, 'scored_count': len(scored), 'total': total,
                'avg_score': None, 'reason': None, 'note': SCORE_NOTE}
        if run.get('status') != 'SUCCESS':
            item['reason'] = 'NOT_SUCCESS'
        elif len(success) < 3:
            item['reason'] = 'TOO_FEW_COMPARABLE_RUNS'
        elif len(scored) < total / 2:
            item['reason'] = 'INSUFFICIENT_METRICS'
        else:
            item['avg_score'] = float(np.mean(list(scored.values())))
        results.append(item)
    return results


def heatmap_frame(scored_runs: list[dict]) -> dict:
    """Plotly heatmap payload: SUCCESS rows only; others listed in ``excluded`` without numbers."""
    shown = [run for run in scored_runs if run.get('status') == 'SUCCESS']
    columns = list(METRIC_DIRECTIONS)
    return {
        'x': columns, 'y': [run['run_id'] for run in shown],
        'z': [[run['percentiles'].get(c) for c in columns] for run in shown],
        'customdata': [[run['raw'].get(c) for c in columns] for run in shown],
        'excluded': [{'run_id': run['run_id'], 'status': run.get('status')} for run in scored_runs if run.get('status') != 'SUCCESS'],
        'note': SCORE_NOTE,
    }


def top_n(scored_runs: list[dict], n: int = 3) -> list[dict]:
    """Best avg_score first, ties by run_id; returns fewer when fewer are scored."""
    valid = [run for run in scored_runs if run.get('status') == 'SUCCESS' and run.get('avg_score') is not None]
    return sorted(valid, key=lambda run: (-run['avg_score'], run['run_id']))[:n]
