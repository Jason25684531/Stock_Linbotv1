"""RESEARCH-SCREEN:V2 market proxy: V1 membership and daily equal weight, total return on raw development bars."""
import numpy as np
import pandas as pd

DEV_END = '2025-12-31'
DEV_YEARS = ('2023', '2024', '2025')
CREDITED = ('cash', 'stock')


def equal_weight_total_return(bars, actions, unresolved=(), *, cost_per_turnover=None):
    """Cash dividends and stock dividends are credited on the ex-date; any other event and every unresolved event date drops that
    stock-day from the equal weight and is counted, never filled in. The primary series carries no cost; with cost_per_turnover the
    turnover-implied net series is a diagnostic only."""
    days = sorted(bars)
    dated = [a['ex_date'] for a in actions] + [e[f] for e in unresolved for f in ('ex_date', 'known_date', 'source_event_date') if e.get(f)]
    if not days or days[-1] > DEV_END or any(d > DEV_END for d in dated):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    cash, ratio, dropped = {}, {}, set()
    for a in actions:
        key = (a['ex_date'], a['stock_id'])
        if a['type'] in CREDITED:
            cash[key] = cash.get(key, 0.0) + float(a.get('cash_per_share') or 0.0)
            ratio[key] = ratio.get(key, 0.0) + float(a.get('stock_ratio') or 0.0)
        else:
            dropped.add(key)
    dropped |= {(e[f], e['stock_id']) for e in unresolved for f in ('ex_date', 'known_date', 'source_event_date') if e.get(f)}
    total, price, turnover, net = {}, {}, {}, {}
    members, excluded, credited = 0, 0, 0
    year_members, year_payers = {}, {}
    drift = {}
    for prev, day in zip(days, days[1:]):
        ret, plain = {}, {}
        for stock, bar in bars[day].items():
            before = bars[prev].get(stock)
            if before is None or not before['close'] > 0:
                continue
            members += 1
            if (day, stock) in dropped:
                excluded += 1
                continue
            key = (day, stock)
            plain[stock] = bar['close'] / before['close'] - 1
            ret[stock] = (bar['close'] * (1 + ratio.get(key, 0.0)) + cash.get(key, 0.0)) / before['close'] - 1
            credited += key in cash
            year_members.setdefault(day[:4], set()).add(stock)
            if cash.get(key):
                year_payers.setdefault(day[:4], set()).add(stock)
        if not ret:
            continue
        stamp = pd.Timestamp(day)
        total[stamp] = float(np.mean(list(ret.values())))
        price[stamp] = float(np.mean(list(plain.values())))
        target = 1 / len(ret)
        traded = 0.5 * (sum(abs(target - drift.get(s, 0.0)) for s in ret) + sum(w for s, w in drift.items() if s not in ret))
        turnover[stamp] = traded if drift else 0.0
        if cost_per_turnover is not None:
            net[stamp] = total[stamp] - turnover[stamp] * cost_per_turnover
        drift = {s: target * (1 + r) / (1 + total[stamp]) for s, r in ret.items()}
    years = {y: {'member_stocks': len(year_members.get(y, ())), 'cash_dividend_stocks': len(year_payers.get(y, ())),
                 'cash_dividend_share': len(year_payers.get(y, ())) / len(year_members[y]) if year_members.get(y) else None}
             for y in DEV_YEARS}
    excluded_share = excluded / members if members else None
    low = [y for y, v in years.items() if v['cash_dividend_share'] is None or v['cash_dividend_share'] < 0.6]
    blocked = []
    if low:
        blocked.append('CASH_DIVIDEND_SHARE_BELOW_60PCT:' + ','.join(low))
    if excluded_share is None or excluded_share > 0.05:
        blocked.append('EXCLUDED_STOCK_DAYS_ABOVE_5PCT')
    total_series = pd.Series(total).sort_index()
    return {'returns': total_series, 'price_returns': pd.Series(price).sort_index(),
            'net_of_cost': pd.Series(net).sort_index() if cost_per_turnover is not None else None,
            'turnover': pd.Series(turnover).sort_index(),
            'diagnostics': {'benchmark_cost': 'NONE', 'member_stock_days': members, 'excluded_stock_days': excluded,
                            'excluded_share': excluded_share, 'credited_dividend_stock_days': credited, 'years': years,
                            'status': 'BLOCKED' if blocked else 'PASS', 'blocked_reasons': blocked}}
