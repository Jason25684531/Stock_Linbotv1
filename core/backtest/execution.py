"""Deterministic-friendly execution pricing and the single fill rule shared by every backtest path and the paper broker.

A bar is {open, high, low, close, volume, amount, limit_up, limit_down}. Anything missing, non-finite or untraded is never
read as a tradable market; a locked open against the order's direction is never filled.
"""
import math
import random

import numpy as np

CAP = 0.05  # max share of the stock's traded value per fill (OD-23)


def apply_slippage(price: float, side: str, max_slippage: float, rng=None) -> float:
    if side not in {"buy", "sell"} or price < 0 or max_slippage < 0:
        raise ValueError("invalid execution input")
    slip = (rng or random).uniform(0, max_slippage)
    return price * (1 + slip if side == "buy" else 1 - slip)


def tick(price):
    return np.select([price < 10, price < 50, price < 100, price < 500, price < 1000], [0.01, 0.05, 0.1, 0.5, 1.0], 5.0)


def limit_prices(reference):
    """TWSE daily limits: +-10 % of the reference price, rounded inside the band to the tick of the limit price."""
    up, down = reference * 1.10, reference * 0.90
    up = np.round(np.floor(up / tick(up) + 1e-9) * tick(up), 2)
    down = np.round(np.ceil(down / tick(down) - 1e-9) * tick(down), 2)
    return up, down


def fill_check(side: str, bar) -> str | None:
    """Rejection reason, or None when the order can trade at the open."""
    if bar is None or any(bar.get(k) is None or not math.isfinite(bar[k]) for k in ('limit_up', 'limit_down')):
        return 'REJECTED_NO_BAR'  # no bar, or no limit band: the market state is unknown
    fields = [bar.get(k) for k in ('open', 'high', 'low', 'close', 'volume', 'amount')]
    if any(v is None or not math.isfinite(v) for v in fields) or bar['volume'] <= 0 or bar['amount'] <= 0 or bar['close'] <= 0:
        return 'REJECTED_NO_TRADE'  # a session without a finite positive close is valued at a stale mark (OD-P5): it never fills either
    locked = bar['high'] == bar['low'] == bar['open']
    if locked and side == 'buy' and bar['open'] >= bar['limit_up']:
        return 'REJECTED_LOCKED_LIMIT_UP'
    if locked and side == 'sell' and bar['open'] <= bar['limit_down']:
        return 'REJECTED_LOCKED_LIMIT_DOWN'
    return None


def cap_shares(shares: int, price: float, prior_amount, cap: float = CAP) -> int:
    """Shares after the participation cap, sized only on what is known before the open: the previous validated session's traded
    value (bar['prior_amount']). The same session's amount is a post-trade diagnostic (same_day_amount_share) and must never be passed
    here. An unknown, non-finite or non-positive prior_amount caps to 0: unknown liquidity is never read as unlimited."""
    if prior_amount is None or not math.isfinite(prior_amount) or prior_amount <= 0:
        return 0
    return min(shares, int(cap * prior_amount / price))


def same_day_amount_share(value, amount) -> float | None:
    """Post-trade liquidity diagnostic: filled value over the session's full traded value (known only after the close)."""
    return value / amount if value and amount and math.isfinite(amount) and amount > 0 else None


def stale_valuation_verdict(events, action_dates=None) -> dict:
    """OD-P5 (Owner rule 2026-10-27): a position without an official quote is marked at its last valid official close, dated
    (price_source_date) and flagged stale (stale_days); no order can fill while unquoted (fill_check rejects NO_BAR / NO_TRADE).
    events: the run's STALE_VALUATION records. action_dates: {stock_id: [ex_date 'YYYY-MM-DD', ...]} of corporate actions, or None
    when unknown. Fail-closed: a stale window that contains a corporate action, or stale marks with unknown actions, is BLOCKED."""
    events = list(events or [])
    if not events:
        return {'status': 'NO_STALE_VALUATION', 'stale_events': 0, 'max_stale_days': 0, 'blocked': []}
    if action_dates is None:
        return {'status': 'VALUATION_BLOCKED_ACTIONS_UNKNOWN', 'stale_events': len(events), 'max_stale_days': max(e['stale_days'] for e in events),
                'blocked': sorted({e['stock_id'] for e in events})}
    blocked = sorted({e['stock_id'] for e in events
                      if any(e['price_source_date'] < d <= e['date'] for d in action_dates.get(e['stock_id'], ()))})
    return {'status': 'VALUATION_BLOCKED_CORPORATE_ACTION' if blocked else 'STALE_LAST_OFFICIAL_CLOSE', 'stale_events': len(events),
            'max_stale_days': max(e['stale_days'] for e in events), 'blocked': blocked}
