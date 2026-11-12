"""Daily positions rebuilt from trades, corporate actions, end positions and cash, reconciled to nav_base; trade liquidity; capacity (design D8).

Session order follows core/backtest/runner.py: corporate actions on the previous close's holdings, then the open fills (sells, then
buys), then the close mark (finite positive close, else the last valid close scaled by later price factors).
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from core.backtest.costs import CostModel
from core.backtest.execution import CAP

TOLERANCE_ABS, TOLERANCE_REL = 0.01, 1e-9


def _closes(bars: pd.DataFrame, stocks) -> dict:
    sub = bars[bars.stock_id.astype(str).isin(set(map(str, stocks)))]
    return {(str(r.stock_id), pd.Timestamp(r.trade_date).strftime('%Y-%m-%d')): float(r.close) for r in sub.itertuples()
            if r.close is not None and math.isfinite(float(r.close)) and float(r.close) > 0}


def rebuild(nav: pd.DataFrame, trades: pd.DataFrame, actions: pd.DataFrame | None, open_positions: list, bars: pd.DataFrame,
            cost_model: dict, initial: float) -> dict:
    costs = CostModel(cost_model['fee_rate'], cost_model.get('tax_rate', 0.0), cost_model.get('minimum_fee', 20))
    dates = [pd.Timestamp(d).strftime('%Y-%m-%d') for d in nav.date]
    buys, sells, booked = defaultdict(list), defaultdict(list), defaultdict(list)
    partial = 0
    for t in (trades if trades is not None else pd.DataFrame()).itertuples():
        buys[str(t.buy_date)[:10]].append((str(t.stock_id), int(t.shares), float(t.buy_price)))
        exit_shares = int(t.exit_shares) if hasattr(t, 'exit_shares') and pd.notna(t.exit_shares) else int(t.shares)
        notional = int(t.shares) * float(t.buy_price)
        total_net = (notional + costs.buy_cost(notional)) * (1 + float(t.profit_pct) / 100)  # runner.sell: profit_pct over total_cost
        dist = float(t.distributions) if hasattr(t, 'distributions') and pd.notna(t.distributions) else 0.0
        sells[str(t.sell_date)[:10]].append((str(t.stock_id), exit_shares, float(t.sell_price), str(t.reason) if hasattr(t, 'reason') else '',
                                              total_net - dist, str(t.buy_date)[:10]))
    if actions is not None and not actions.empty:
        for a in actions[actions.status == 'BOOKED'].itertuples():
            booked[str(a.date)[:10]].append(a)
    for p in open_positions or []:  # entry shares: shares_before of the first booked event after the buy, else the current shares
        sid, buy_date = str(p['stock_id']), str(p['buy_date'])[:10]
        later = sorted((d, a) for d, rows in booked.items() for a in rows if str(a.stock_id) == sid and d > buy_date)
        shares = int(later[0][1].shares_before) if later else int(p['shares'])
        buys[buy_date].append((sid, shares, float(p['entry_price'])))
    stocks = {s for v in buys.values() for s, _, _ in v}
    close = _closes(bars, stocks)
    held, cash, daily, mismatches, reasons, windows = {}, float(initial), [], [], set(), []
    for date, target in zip(dates, nav.nav_base.astype(float)):
        for a in booked.get(date, ()):
            h = held.get(str(a.stock_id))
            if h is None:
                continue
            if h['shares'] != int(a.shares_before):
                reasons.add('CORPORATE_ACTION_SHARES_BEFORE_DIFFER')
            h['shares'] = int(a.shares_after)
            h['mark'] = h['mark'] * float(a.price_factor) if h['mark'] is not None else None
            cash += float(a.cash_credited)
            if h['shares'] <= 0:
                held.pop(str(a.stock_id))
        for sid, shares, price, reason, sale_net, bought in sells.get(date, ()):
            h = held.pop(sid, None)
            if h is None:
                if not reason.startswith('CORPORATE_ACTION_CASH_OUT'):
                    reasons.add('SELL_WITHOUT_HOLDING')
                continue
            revenue = shares * price
            full = revenue - costs.sell_cost(revenue) if shares else 0.0
            # runner.sell writes exit_shares only for positions that had a corporate action, so a partial fill under the participation
            # cap is detected from the money: the trade's total net (profit_pct) differs from one full sale at the final price.
            if h['shares'] != shares or abs(sale_net - full) > 0.5:
                partial += 1
                windows.append((bought, date))
            cash += sale_net
        for sid, shares, price in buys.get(date, ()):
            notional = shares * price
            cash -= notional + costs.buy_cost(notional)
            held[sid] = {'shares': shares, 'mark': None}
        values = {}
        for sid, h in held.items():
            c = close.get((sid, date))
            if c is not None:
                h['mark'] = c
            if h['mark'] is None:
                reasons.add('NO_VALID_CLOSE_SINCE_ENTRY')
                values[sid] = math.nan
            else:
                values[sid] = h['shares'] * h['mark']
        total = cash + sum(values.values())
        ok = math.isfinite(total) and abs(total - target) <= TOLERANCE_ABS + TOLERANCE_REL * abs(target)
        if not ok:
            mismatches.append(date)
        daily.append({'date': date, 'cash': cash, 'values': values, 'nav': target})
    if partial:
        reasons.add(f'PARTIAL_EXITS_NOT_STORED:{partial}')
    end = {str(p['stock_id']): int(p['shares']) for p in open_positions or []}
    if {s: h['shares'] for s, h in held.items()} != end:
        reasons.add('END_POSITIONS_DIFFER')
        if dates:
            mismatches.append(dates[-1])
    if mismatches:
        outside = sorted(d for d in set(mismatches) if not any(b <= d < s for b, s in windows))
        return {'status': 'POSITIONS_UNVERIFIABLE', 'first_mismatch_date': min(mismatches), 'mismatch_sessions': len(set(mismatches)),
                'mismatch_outside_partial_exit_windows': len(outside), 'first_unexplained_mismatch': outside[0] if outside else None,
                'sessions': len(dates), 'reasons': sorted(reasons) or ['NAV_DIFFERS'], 'tolerance': f'abs <= {TOLERANCE_ABS} + {TOLERANCE_REL} x NAV'}
    rows = []
    for d in daily:
        weights = {s: val / d['nav'] for s, val in d['values'].items()} if d['nav'] > 0 else {}
        invested = sum(d['values'].values())
        rows.append({'date': d['date'], 'holdings': sorted(d['values']), 'count': len(d['values']), 'cash_share': d['cash'] / d['nav'] if d['nav'] > 0 else None,
                     'max_weight': max(weights.values()) if weights else 0.0,
                     'hhi': sum((val / invested) ** 2 for val in d['values'].values()) if invested > 0 else None})
    return {'status': 'RECONCILED', 'sessions': len(dates), 'reasons': [], 'daily': rows, 'tolerance': f'abs <= {TOLERANCE_ABS} + {TOLERANCE_REL} x NAV'}


def position_summary(rebuilt: dict) -> dict:
    keys = ('avg_holdings', 'max_holdings', 'avg_max_weight', 'max_weight', 'avg_hhi', 'avg_cash_share', 'invested_share_of_sessions')
    if rebuilt['status'] != 'RECONCILED':
        return {k: {'value': None, 'reason': 'POSITIONS_UNVERIFIABLE'} for k in keys} | {'status': rebuilt['status']}
    d = pd.DataFrame(rebuilt['daily'])
    val = lambda x: {'value': float(x), 'reason': None} if x is not None and math.isfinite(float(x)) else {'value': None, 'reason': 'NO_HOLDINGS'}
    return {'status': 'RECONCILED', 'avg_holdings': val(d['count'].mean()), 'max_holdings': val(d['count'].max()),
            'avg_max_weight': val(d.loc[d['count'] > 0, 'max_weight'].mean() if (d['count'] > 0).any() else None), 'max_weight': val(d.max_weight.max()),
            'avg_hhi': val(d.hhi.dropna().mean() if d.hhi.notna().any() else None), 'avg_cash_share': val(d.cash_share.dropna().mean()),
            'invested_share_of_sessions': val((d['count'] > 0).mean())}


def liquidity(trades: pd.DataFrame, bars: pd.DataFrame) -> dict:
    """Trade notional against the stock's traded value: the session before the fill (the engine's cap basis) and the fill session."""
    if trades is None or trades.empty:
        return {'trades': 0, 'reason': 'NO_TRADES'}
    sub = bars[bars.stock_id.astype(str).isin(set(trades.stock_id.astype(str)))].sort_values(['stock_id', 'trade_date'])
    groups = {sid: g.reset_index(drop=True) for sid, g in sub.groupby(sub.stock_id.astype(str))}
    entry_prev, entry_same, exit_same, missing, no_trade_days, one_price_days, held_days, amounts = [], [], [], 0, 0, 0, 0, []
    for t in trades.itertuples():
        g = groups.get(str(t.stock_id))
        buy = pd.Timestamp(t.buy_date)
        if g is None or not (g.trade_date == buy).any():
            missing += 1
            continue
        i = int(np.flatnonzero(g.trade_date.to_numpy() == buy.to_datetime64())[0])
        notional = float(t.shares) * float(t.buy_price)
        if i > 0 and g.amount.iloc[i - 1] > 0:
            entry_prev.append(notional / float(g.amount.iloc[i - 1]))
        if g.amount.iloc[i] > 0:
            entry_same.append(notional / float(g.amount.iloc[i]))
        sell = pd.Timestamp(t.sell_date) if pd.notna(t.sell_date) else None
        if sell is not None:
            row = g[g.trade_date == sell]
            shares = float(t.exit_shares) if hasattr(t, 'exit_shares') and pd.notna(t.exit_shares) else float(t.shares)
            if len(row) and row.amount.iloc[0] > 0 and shares:
                exit_same.append(shares * float(t.sell_price) / float(row.amount.iloc[0]))
            span = g[(g.trade_date >= buy) & (g.trade_date <= sell)]
            held_days += len(span)
            no_trade_days += int(((span.volume.fillna(0) <= 0) | (span.status != 'TRADED')).sum())
            one_price_days += int(((span.high == span.low) & (span.volume > 0)).sum())
            amounts.extend(span.amount.dropna().astype(float).tolist())
    dist = lambda xs: {'p50': float(np.median(xs)), 'p90': float(np.percentile(xs, 90)), 'max': float(np.max(xs)), 'n': len(xs)} if xs else {'reason': 'NO_BARS'}
    return {'trades': int(len(trades)), 'trades_without_bar': missing, 'entry_participation_prev': dist(entry_prev),
            'cap_binding_share': float(np.mean(np.array(entry_prev) >= CAP * 0.98)) if entry_prev else None,
            'entry_participation_same': dist(entry_same), 'exit_participation_same': dist(exit_same),
            'held_sessions': held_days, 'held_no_trade_sessions': no_trade_days, 'held_one_price_sessions': one_price_days,
            'held_avg_traded_value': float(np.mean(amounts)) if amounts else None,
            'definitions': {'participation': 'trade notional / the stock traded value (成交金額) of the session before the fill (engine cap basis) or the fill session',
                            'one_price': 'high == low with volume > 0 (typically a limit-locked session)', 'no_trade': 'volume 0 or status other than TRADED'}}


def capacity(liq: dict, initial: float, reconciled: bool, max_missing_share=0.0, min_trades: int = 0) -> dict:
    """Capital at which the 90th percentile entry participation reaches the engine cap, assuming participation scales linearly."""
    assumption = (f'線性放大假設：參與率與資金成正比；以前一交易日成交金額為基準，使第 90 百分位進場參與率等於引擎上限 {CAP:.0%}。'
                  '沒有盤中或委託簿資料，不代表真實可成交量。')
    if not reconciled:
        return {'label': 'POSITIONS_UNVERIFIABLE', 'value': None, 'reason': 'POSITIONS_UNVERIFIABLE', 'assumption': assumption}
    if liq.get('trades', 0) < min_trades:  # a percentile of a handful of fills extrapolated to a capital size says nothing
        return {'label': 'CAPACITY_UNVERIFIED', 'value': None, 'reason': f"INSUFFICIENT_SAMPLE:{liq.get('trades', 0)}<{min_trades}", 'assumption': assumption}
    p90 = liq.get('entry_participation_prev', {}).get('p90')
    if not liq.get('trades') or p90 is None or liq['trades_without_bar'] > max_missing_share * liq['trades'] or p90 <= 0:
        return {'label': 'CAPACITY_UNVERIFIED', 'value': None, 'reason': 'BARS_MISSING_FOR_TRADES', 'assumption': assumption}
    return {'label': 'CAPACITY_ESTIMATE_ASSUMED', 'value': float(initial * CAP / p90), 'reason': None, 'assumption': assumption, 'cap': CAP}
