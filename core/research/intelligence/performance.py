"""Performance, risk and benchmark statistics of one stored run, on the VERIFIED or PROVISIONAL track (design D5, D6).

Reuses core.backtest.metrics.calculate_metrics; adds only what it lacks. Every undefined value is {'value': None, 'reason': ...}.
Annualization: 252 sessions, risk-free rate 0, daily simple returns of nav_base (costs included).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from core.backtest.costs import CostModel
from core.backtest.metrics import calculate_metrics

SESSIONS = 252
ANNUALIZATION = {'sessions_per_year': SESSIONS, 'risk_free_rate': 0.0, 'returns': 'daily simple returns of nav_base, costs included'}


def v(value, reason=None) -> dict:
    ok = value is not None and math.isfinite(float(value))
    return {'value': float(value) if ok else None, 'reason': None if ok else (reason or 'UNDEFINED')}


def year_status(cells) -> dict:
    return {int(c['year']): c['status'] for c in cells}


def verified_span(status: dict, years) -> tuple[int, int] | None:
    """Longest run of consecutive VERIFIED years among the run's years (earliest wins a tie)."""
    best, current = None, None
    for year in sorted(set(years)):
        if status.get(year) == 'VERIFIED':
            current = (current[0], year) if current and current[1] == year - 1 else (year, year)
            if best is None or current[1] - current[0] > best[1] - best[0]:
                best = current
        else:
            current = None
    return best


def slice_track(nav: pd.DataFrame, first_year: int, last_year: int) -> pd.DataFrame:
    """Sessions of [first_year, last_year] plus the previous close as the base, so the first session's return is included."""
    nav = nav.assign(date=pd.to_datetime(nav.date)).sort_values('date').reset_index(drop=True)
    inside = nav[(nav.date.dt.year >= first_year) & (nav.date.dt.year <= last_year)]
    before = nav[nav.date < inside.date.min()].tail(1) if len(inside) else nav.iloc[:0]
    return pd.concat([before, inside]).reset_index(drop=True)


def drawdown_episodes(dates, equity) -> list[dict]:
    """Every peak-to-recovery episode; the last one may be unrecovered (recovery_date None)."""
    dates, out = list(pd.to_datetime(dates)), []
    peak_i, trough_i, inside = 0, None, False

    def close(recovery_i):
        row = {'peak_date': dates[peak_i].strftime('%Y-%m-%d'), 'trough_date': dates[trough_i].strftime('%Y-%m-%d'),
               'depth': equity[trough_i] / equity[peak_i] - 1, 'peak_to_trough_sessions': trough_i - peak_i}
        if recovery_i is None:
            return row | {'recovery_date': None, 'trough_to_recovery_sessions': None, 'peak_to_recovery_sessions': None,
                          'underwater_sessions': len(equity) - 1 - peak_i, 'status': 'NOT_RECOVERED'}
        return row | {'recovery_date': dates[recovery_i].strftime('%Y-%m-%d'), 'trough_to_recovery_sessions': recovery_i - trough_i,
                      'peak_to_recovery_sessions': recovery_i - peak_i, 'underwater_sessions': recovery_i - peak_i, 'status': 'RECOVERED'}
    for i, value in enumerate(equity):
        if value >= equity[peak_i]:
            if inside:
                out.append(close(i))
                inside = False
            peak_i = i
        elif not inside:
            inside, trough_i = True, i
        elif value < equity[trough_i]:
            trough_i = i
    if inside:
        out.append(close(None))
    return out


def _held_sessions(trades: pd.DataFrame, sessions: pd.DatetimeIndex) -> np.ndarray:
    return sessions.searchsorted(pd.to_datetime(trades.sell_date)) - sessions.searchsorted(pd.to_datetime(trades.buy_date))


def trade_money(trades: pd.DataFrame, cost_model: dict | None) -> pd.Series | None:
    """profit = profit_pct x basis, basis = entry notional + buy fee (runner.sell: basis is total_cost incl. the buy fee)."""
    if trades is None or trades.empty or cost_model is None or not {'buy_price', 'shares'} <= set(trades):
        return None
    costs = CostModel(cost_model['fee_rate'], cost_model.get('tax_rate', 0.0), cost_model.get('minimum_fee', 20))
    notional = trades.buy_price.astype(float) * trades.shares.astype(float)
    basis = notional + notional.map(costs.buy_cost)
    return trades.profit_pct.astype(float) / 100 * basis


def _cagr(equity) -> dict:
    if len(equity) < 2:
        return v(None, 'AT_LEAST_TWO_SESSIONS')
    if min(equity) <= 0:
        return v(None, 'NON_POSITIVE_EQUITY')
    return v((equity[-1] / equity[0]) ** (SESSIONS / (len(equity) - 1)) - 1)


def summarize(nav: pd.DataFrame, trades: pd.DataFrame | None, bench: pd.DataFrame | None, cost_model: dict | None = None) -> dict:
    """Metrics of one contiguous track. Trades counted are those that closed inside the track (after its base session)."""
    nav = nav.assign(date=pd.to_datetime(nav.date)).sort_values('date').reset_index(drop=True)
    equity, stress = nav.nav_base.astype(float).tolist(), nav.nav_stress.astype(float).tolist()
    sessions = pd.DatetimeIndex(nav.date)
    closed = pd.DataFrame()
    if trades is not None and not trades.empty:
        sell = pd.to_datetime(trades.sell_date)
        closed = trades[(sell > sessions[0]) & (sell <= sessions[-1])].reset_index(drop=True)
    held = [] if closed.empty else closed.held_sessions if 'held_sessions' in closed else _held_sessions(closed, sessions)  # full-run sessions when supplied
    rows = [] if closed.empty else [{'profit_pct': float(p), 'days': int(d)} for p, d in zip(closed.profit_pct, held)]
    base = calculate_metrics(equity, rows).values
    out = {name: {'value': m.value, 'reason': m.reason} for name, m in base.items() if name not in ('exposure', 'turnover', 'cagr', 'calmar')}
    if min(equity) <= 0:  # metrics.py computes ratios on a curve through zero; none of them is meaningful there
        for name in ('sharpe', 'sortino', 'annualized_return', 'annualized_volatility', 'downside_deviation'):
            out[name] = v(None, 'NON_POSITIVE_EQUITY')
    out['cagr'], out['cagr_stress'] = _cagr(equity), _cagr(stress)
    days = (sessions[-1] - sessions[0]).days
    out['cagr_calendar'] = v((equity[-1] / equity[0]) ** (365.25 / days) - 1) if days > 0 and min(equity) > 0 else v(None, 'NON_POSITIVE_EQUITY' if days > 0 else 'AT_LEAST_TWO_SESSIONS')
    out['cost_drag'] = v(out['cagr']['value'] - out['cagr_stress']['value'], 'UNDEFINED_COMPONENT') if None not in (out['cagr']['value'], out['cagr_stress']['value']) else v(None, 'UNDEFINED_COMPONENT')
    mdd = out['max_drawdown']['value']
    out['calmar'] = v(out['cagr']['value'] / abs(mdd)) if out['cagr']['value'] is not None and mdd else v(None, 'MAX_DRAWDOWN_ZERO' if mdd == 0 else 'UNDEFINED_COMPONENT')
    profits = [r['profit_pct'] / 100 for r in rows]
    out['expectancy'] = v(float(np.mean(profits))) if profits else v(None, 'NO_CLOSED_TRADES')
    wins, losses = [p for p in profits if p > 0], [p for p in profits if p < 0]
    out['avg_win'] = v(float(np.mean(wins))) if wins else v(None, 'NO_WINNING_TRADES')
    out['avg_loss'] = v(float(np.mean(losses))) if losses else v(None, 'NO_LOSING_TRADES')
    money = trade_money(closed, cost_model)
    if money is None or money.empty:
        out['top5_profit_share'] = v(None, 'NO_CLOSED_TRADES' if closed.empty else 'TRADE_SIZE_UNAVAILABLE')
    else:
        total = money.sum()
        out['top5_profit_share'] = v(money.nlargest(5).sum() / total) if total > 0 else v(None, 'NON_POSITIVE_TOTAL_PROFIT')
        out['top5_profit_share']['definition'] = 'net profit of the 5 most profitable closed trades / net profit of all closed trades (above 100% when the rest lose)'
    episodes = drawdown_episodes(sessions, equity)
    out['drawdown_episodes'] = sorted(episodes, key=lambda e: e['depth'])[:10]
    recovered = [e['peak_to_recovery_sessions'] for e in episodes if e['status'] == 'RECOVERED']
    open_ep = [e for e in episodes if e['status'] == 'NOT_RECOVERED']
    out['longest_recovery_sessions'] = v(max(recovered)) if recovered else v(None, 'NO_RECOVERED_DRAWDOWN')
    out['current_underwater_sessions'] = v(open_ep[0]['underwater_sessions']) if open_ep else v(0)
    curve = pd.Series(equity)
    out['average_drawdown'] = v(float((curve / curve.cummax() - 1).mean())) if min(equity) > 0 else v(None, 'NON_POSITIVE_EQUITY')
    yearly = _yearly(nav)
    out['yearly'] = yearly
    base_years = [y['base'] for y in yearly.values() if y['base'] is not None]
    out['worst_year'] = v(min(base_years)) if base_years else v(None, 'NO_YEAR')
    stress_years = [y['stress'] for y in yearly.values() if y['stress'] is not None]
    out['positive_year_share_stress'] = v(sum(r > 0 for r in stress_years) / len(stress_years)) if stress_years else v(None, 'NO_YEAR')
    rolling = (curve / curve.shift(SESSIONS) - 1).dropna() if min(equity) > 0 else pd.Series(dtype=float)
    out['rolling_252'] = {'windows': int(len(rolling)), **{k: v(f(rolling) if len(rolling) else None, 'NO_FULL_WINDOW') for k, f in
                          (('min', np.min), ('median', np.median), ('max', np.max), ('share_positive', lambda r: float((r > 0).mean())))}}
    out.update(_benchmark(nav, bench, yearly))
    out.update(_activity(nav, trades, sessions))
    out['period'] = [sessions[min(1, len(sessions) - 1)].strftime('%Y-%m-%d'), sessions[-1].strftime('%Y-%m-%d')]
    out['base_date'] = sessions[0].strftime('%Y-%m-%d')
    out['sessions'] = len(sessions) - 1
    out['closed_trades'] = len(rows)
    out['annualization'] = ANNUALIZATION
    return out


def _activity(nav: pd.DataFrame, trades: pd.DataFrame | None, sessions: pd.DatetimeIndex) -> dict:
    """How much of the track the strategy was actually invested: sessions with a position open at the close, zero-return sessions,
    the last exit inside the track. A flat track (cash) has returns of exactly zero and says nothing about the strategy."""
    days = sessions[1:]
    held = np.zeros(len(days), dtype=bool)
    last = None
    if trades is not None and not trades.empty:
        for buy, sell in zip(pd.to_datetime(trades.buy_date), pd.to_datetime(trades.sell_date)):
            held |= (days >= buy) & (days < sell)
        inside = pd.to_datetime(trades.sell_date)
        inside = inside[(inside > sessions[0]) & (inside <= sessions[-1])]
        last = inside.max().strftime('%Y-%m-%d') if len(inside) else None
    r = nav.nav_base.astype(float).pct_change().to_numpy()[1:]
    return {'invested_session_share': v(held.mean()) if len(days) else v(None, 'NO_SESSIONS'),
            'zero_return_session_share': v(float((r == 0).mean())) if len(r) else v(None, 'NO_SESSIONS'),
            'last_exit_in_track': last}


def _yearly(nav: pd.DataFrame) -> dict:
    """Return of each year from the previous close (the track's first row for the first year) to its last session.
    A year holding only the base row (the previous close of a clipped track) has no return of its own."""
    out, prev = {}, nav.iloc[0] if len(nav) else None
    for year, group in nav.groupby(nav.date.dt.year):
        last = group.iloc[-1]
        if last.name == 0:
            continue
        out[str(year)] = {'base': last.nav_base / prev.nav_base - 1 if prev.nav_base > 0 else None,
                          'stress': last.nav_stress / prev.nav_stress - 1 if prev.nav_stress > 0 else None,
                          'sessions': int(len(group) - (group.index[0] == 0)), 'from': prev.date.strftime('%Y-%m-%d'), 'to': last.date.strftime('%Y-%m-%d')}
        prev = last
    return out


def _benchmark(nav: pd.DataFrame, bench: pd.DataFrame | None, yearly: dict | None = None) -> dict:
    keys = ('benchmark_cagr_total_return', 'benchmark_cagr_price', 'benchmark_max_drawdown_price', 'benchmark_max_drawdown_total_return',
            'excess_cagr', 'downside_capture', 'benchmark_worst_year_total_return')
    if bench is None or bench.empty:
        return {k: v(None, 'BENCHMARK_MISSING') for k in keys} | {'benchmark_label': None}
    b = bench.assign(date=pd.to_datetime(bench.date)).set_index('date').reindex(pd.to_datetime(nav.date))
    missing = int(b.taiex_total_return.isna().sum())
    if missing:
        return {k: v(None, f'BENCHMARK_MISSING_SESSIONS:{missing}') for k in keys} | {'benchmark_label': None}
    tri, price = b.taiex_total_return.astype(float).tolist(), b.taiex_price.astype(float).tolist()
    mdd = lambda s: float((pd.Series(s) / pd.Series(s).cummax() - 1).min())
    out = {'benchmark_cagr_total_return': _cagr(tri), 'benchmark_cagr_price': _cagr(price),
           'benchmark_max_drawdown_price': v(mdd(price)), 'benchmark_max_drawdown_total_return': v(mdd(tri)),
           'benchmark_label': 'TOTAL_RETURN_INDEX: TAIEX total return index (dividends reinvested) for excess return; PRICE_INDEX_ONLY (no dividends) shown alongside, never used for excess return'}
    mine = _cagr(nav.nav_base.astype(float).tolist())
    out['excess_cagr'] = v(mine['value'] - out['benchmark_cagr_total_return']['value']) if mine['value'] is not None and out['benchmark_cagr_total_return']['value'] is not None else v(None, 'UNDEFINED_COMPONENT')
    r = nav.nav_base.astype(float).pct_change().to_numpy()[1:]
    br = pd.Series(tri).pct_change().to_numpy()[1:]
    down = br < 0
    out['downside_capture'] = v(r[down].sum() / br[down].sum()) if down.any() else v(None, 'NO_BENCHMARK_DOWN_SESSIONS')
    out['downside_capture']['definition'] = 'sum of strategy daily returns on sessions the total return index fell / sum of the index daily returns on those sessions'
    level = pd.Series(tri, index=pd.to_datetime(nav.date))
    yr = [level[pd.Timestamp(y['to'])] / level[pd.Timestamp(y['from'])] - 1 for y in (yearly or {}).values()]  # the strategy's own year periods
    out['benchmark_worst_year_total_return'] = v(min(yr)) if yr else v(None, 'NO_YEAR')
    return out


def trade_level(trades: pd.DataFrame | None, bars: pd.DataFrame | None, actions: pd.DataFrame | None, sessions=None, marks: dict | None = None,
                cost_model: dict | None = None, horizons=(5, 10, 20)) -> dict:
    """MFE/MAE over the sessions held before the exit session, and returns N official sessions after the entry session, on raw prices.
    Excluded and counted: trades with a booked corporate action while held, windows that contain an ex-date or capital change of the
    stock (marks), and windows whose end has no valid close. Net forward return subtracts one round trip of fee and tax."""
    keys = ['mfe_median', 'mae_median'] + [f'forward_return_{h}' for h in horizons] + [f'forward_return_{h}_net' for h in horizons]
    if trades is None or trades.empty or bars is None or bars.empty:
        return {k: v(None, 'NO_TRADES_OR_BARS') for k in keys} | {'trades_used': 0, 'trades_excluded_corporate_action': 0}
    with_ca = trades.distributions.notna() & (trades.distributions.astype(float) != 0) if 'distributions' in trades else pd.Series(False, index=trades.index)
    clean = trades[~with_ca]
    if clean.empty:
        return {k: v(None, f'NO_TRADES_WITHOUT_CORPORATE_ACTION:{int(with_ca.sum())}') for k in keys} | \
            {'trades_used': 0, 'trades_excluded_corporate_action': int(with_ca.sum())}
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(sessions))) if sessions is not None else pd.DatetimeIndex(sorted(bars.trade_date.unique()))
    sub = bars[bars.stock_id.astype(str).isin(set(clean.stock_id.astype(str)))]
    close = {(str(r.stock_id), pd.Timestamp(r.trade_date)): float(r.close) for r in sub.itertuples() if pd.notna(r.close) and float(r.close) > 0}
    by_stock = {sid: g.sort_values('trade_date').reset_index(drop=True) for sid, g in sub.groupby(sub.stock_id.astype(str))}
    round_trip = 2 * cost_model['fee_rate'] + cost_model.get('tax_rate', 0.0) if cost_model else None
    mfe, mae, fwd, skipped = [], [], {h: [] for h in horizons}, {'no_valid_close': 0, 'event_in_window': 0, 'beyond_data': 0}
    for t in clean.itertuples():
        sid, g = str(t.stock_id), by_stock.get(str(t.stock_id))
        buy, sell, entry = pd.Timestamp(t.buy_date), pd.Timestamp(t.sell_date), float(t.buy_price)
        if g is None or entry <= 0:
            continue
        held = g[(g.trade_date >= buy) & (g.trade_date < sell)]
        if len(held) and held.high.notna().any():
            mfe.append(held.high.astype(float).max() / entry - 1)
            mae.append(held.low.astype(float).min() / entry - 1)
        i = cal.searchsorted(buy)
        for h in horizons:
            if i + h >= len(cal):
                skipped['beyond_data'] += 1
                continue
            end = cal[i + h]
            if marks and len(marks.get(sid, [])) and ((marks[sid] > buy) & (marks[sid] <= end)).any():
                skipped['event_in_window'] += 1
                continue
            if (sid, end) not in close:
                skipped['no_valid_close'] += 1
                continue
            fwd[h].append(close[(sid, end)] / entry - 1)
    out = {'mfe_median': v(np.median(mfe)) if mfe else v(None, 'NO_BARS_FOR_TRADES'),
           'mae_median': v(np.median(mae)) if mae else v(None, 'NO_BARS_FOR_TRADES')}
    for h in horizons:
        out[f'forward_return_{h}'] = v(np.mean(fwd[h])) if fwd[h] else v(None, 'NO_FORWARD_SESSIONS')
        out[f'forward_return_{h}_net'] = (v(np.mean(fwd[h]) - round_trip) if round_trip is not None else v(None, 'NO_COST_MODEL')) if fwd[h] else v(None, 'NO_FORWARD_SESSIONS')
        out[f'forward_positive_share_{h}'] = v(np.mean(np.array(fwd[h]) > 0)) if fwd[h] else v(None, 'NO_FORWARD_SESSIONS')
    return out | {'trades_used': len(clean), 'trades_excluded_corporate_action': int(with_ca.sum()), 'forward_skipped': skipped, 'round_trip_cost': round_trip,
                  'method': 'raw OHLC from the snapshot; MFE = max high / entry - 1 and MAE = min low / entry - 1 over the sessions from the buy session to the one '
                            'before the exit session; forward = close N official sessions after the buy session / entry - 1; net = forward - (2 x fee rate + tax rate)'}
