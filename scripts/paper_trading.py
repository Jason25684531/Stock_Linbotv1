"""Paper trading: simulated orders, fills, positions, cash, risk limits, kill switch and reconciliation. Never touches a broker.

Flow per official session D (driven by scripts/daily_runner.py after the research-shadow signal):
  1. execute(D)  — orders planned at D-1's close fill at D's open, or are rejected with a reason (never assumed filled):
                   no bar / no trade / open locked at the limit against the order / no cash. Fills are capped at CAP of D's traded value.
  2. mark(D)     — NAV at D's close; kill switch on drawdown or a held name without a close (HALTED: no new orders).
  3. plan(D)     — on rebalance days, target weights from D's ranking -> orders for D+1 (sells first, then buys).
  4. reconcile() — replay of the order log must equal the stored cash and positions, else HALTED.
State and logs live in one directory; every write is atomic, every session is processed once (idempotent by date).
Label PAPER_REHEARSAL: the strategy is unqualified (NO_QUALIFIED_SELECTION); this tests the trading machinery, it is not performance evidence.
"""
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.settings import Config  # noqa: E402
from core.backtest.costs import CostModel  # noqa: E402
from core.backtest.execution import CAP, cap_shares, fill_check, same_day_amount_share  # noqa: E402,F401  (the one fill rule, shared with the backtests)

LABEL = 'PAPER_REHEARSAL'
COSTS = CostModel(Config.FEE_RATE, Config.TAX_RATE, 20.0)
SLIPPAGE = Config.SLIPPAGE_RATE
MAX_WEIGHT = 0.10
CASH_BUFFER = 0.01
MAX_DRAWDOWN = 0.25  # kill switch: no new orders once NAV is 25 % below its peak (Owner sets the live value; this is the rehearsal default)


def _save(path: Path, obj):
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def _log(path: Path, record):
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def load(root) -> dict:
    return json.loads((Path(root) / 'state.json').read_text(encoding='utf-8'))


def open_account(root, cash: float, strategy: str) -> dict:
    root = Path(root)
    if (root / 'state.json').is_file():
        return load(root)  # never reset an existing account
    root.mkdir(parents=True, exist_ok=True)
    state = {'label': LABEL, 'broker': 'DISABLED', 'strategy': strategy, 'initial_cash': cash, 'cash': cash, 'positions': {}, 'pending': [],
             'peak_nav': cash, 'status': 'ACTIVE', 'halt_reason': None, 'executed': [], 'marked': [], 'planned': []}
    _save(root / 'state.json', state)
    return state


def halt(root, reason):
    state = load(root)
    state.update(status='HALTED', halt_reason=reason)
    _save(Path(root) / 'state.json', state)
    return state


def execute(root, date: str, bars: dict, previous_session: str | None = None) -> list:
    """Fill the pending orders at `date`'s open. bars: stock -> {open, high, low, close, volume, amount, prior_amount, limit_up, limit_down}.
    Orders planned on any session other than `previous_session` are stale (a skipped day) and are rejected, never filled late."""
    root, state = Path(root), load(root)
    if date in state['executed']:
        return []
    results = []
    for order in sorted(state['pending'], key=lambda o: o['side'] != 'sell'):  # sells first: their cash funds the buys
        bar = bars.get(order['stock_id'])
        stale = previous_session is not None and order['plan_date'] != previous_session
        reason = 'REJECTED_STALE_PLAN' if stale else fill_check(order['side'], bar)
        shares, price, cost = 0, None, 0.0
        if reason is None:
            price = bar['open'] * (1 + SLIPPAGE if order['side'] == 'buy' else 1 - SLIPPAGE)
            shares = cap_shares(order['shares'], price, bar.get('prior_amount'))  # sized before the open; the day's amount is only reported
            if order['side'] == 'sell':
                shares = min(shares, state['positions'].get(order['stock_id'], 0))
                cost = COSTS.sell_cost(shares * price) if shares else 0.0
                state['cash'] += shares * price - cost
            else:
                while shares > 0 and shares * price + COSTS.buy_cost(shares * price) > state['cash']:
                    shares -= 1  # same reduction as the research engine: the largest share count whose gross plus fee fits the cash
                cost = COSTS.buy_cost(shares * price) if shares else 0.0
                state['cash'] -= shares * price + cost
            held = state['positions'].get(order['stock_id'], 0) + (shares if order['side'] == 'buy' else -shares)
            state['positions'] = {**state['positions'], order['stock_id']: held} if held else {k: v for k, v in state['positions'].items() if k != order['stock_id']}
            reason = 'FILLED' if shares == order['shares'] else ('PARTIAL' if shares else 'REJECTED_CAP_OR_CASH')
        record = {**order, 'exec_date': date, 'status': reason, 'filled_shares': shares, 'price': price, 'cost': cost,
                  'same_day_amount_share': same_day_amount_share(shares * (price or 0), (bar or {}).get('amount'))}
        _log(root / 'orders.jsonl', record)
        results.append(record)
    state['pending'] = []
    state['executed'].append(date)
    _save(root / 'state.json', state)
    return results


def mark(root, date: str, closes: dict) -> dict:
    """NAV at the close; HALTED on a missing close for a held name or on drawdown beyond MAX_DRAWDOWN."""
    root, state = Path(root), load(root)
    if date in state['marked']:
        return state
    missing = [s for s in state['positions'] if closes.get(s) is None]
    nav = state['cash'] + sum(n * closes.get(s, 0.0) for s, n in state['positions'].items())
    state['peak_nav'] = max(state['peak_nav'], nav)
    drawdown = nav / state['peak_nav'] - 1
    if missing:
        state.update(status='HALTED', halt_reason=f'{date}: no close for held {missing}')
    elif drawdown <= -MAX_DRAWDOWN:
        state.update(status='HALTED', halt_reason=f'{date}: drawdown {drawdown:.1%} beyond {MAX_DRAWDOWN:.0%}')
    state['marked'].append(date)
    _save(root / 'state.json', state)
    _log(root / 'nav.jsonl', {'date': date, 'nav': nav, 'cash': state['cash'], 'drawdown': drawdown, 'positions': len(state['positions']), 'status': state['status']})
    return state


def plan(root, date: str, weights: dict, closes: dict) -> list:
    """Orders for the next session that move holdings toward `weights` (capped at MAX_WEIGHT). Nothing is planned while HALTED."""
    root, state = Path(root), load(root)
    if state['status'] != 'ACTIVE' or date in state['planned']:
        return []
    nav = state['cash'] + sum(n * closes[s] for s, n in state['positions'].items())
    investable = nav * (1 - CASH_BUFFER)  # fees and slippage must not turn the last buy into a partial fill
    target = {s: int(investable * min(w, MAX_WEIGHT) / closes[s]) for s, w in weights.items() if closes.get(s)}
    orders = []
    for stock in sorted(set(target) | set(state['positions'])):
        delta = target.get(stock, 0) - state['positions'].get(stock, 0)
        if delta:
            orders.append({'stock_id': stock, 'side': 'buy' if delta > 0 else 'sell', 'shares': abs(delta), 'plan_date': date})
    state['pending'] = orders
    state['planned'].append(date)
    _save(root / 'state.json', state)
    return orders


def reconcile(root) -> dict:
    """Replay the order log from the initial cash; any difference from the stored state halts the account."""
    root, state = Path(root), load(root)
    cash, positions = state['initial_cash'], {}
    path = root / 'orders.jsonl'
    for o in (json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line) if path.is_file() else []:
        n = o['filled_shares']
        if not n:
            continue
        sign = 1 if o['side'] == 'buy' else -1
        cash -= sign * n * o['price'] + o['cost']
        positions[o['stock_id']] = positions.get(o['stock_id'], 0) + sign * n
    positions = {k: v for k, v in positions.items() if v}
    ok = abs(cash - state['cash']) < 1e-6 and positions == state['positions']
    if not ok and state['status'] != 'HALTED':
        halt(root, f'reconciliation failed: replay cash {cash:.2f} vs {state["cash"]:.2f}, positions equal {positions == state["positions"]}')
    return {'status': 'PASS' if ok else 'FAIL', 'replay_cash': cash, 'state_cash': state['cash']}


def session_bars(day: str, days) -> tuple[dict, dict]:
    """Official MI_INDEX bars for `day` with TWSE limit prices from the previous official close; and the closes."""
    import pandas as pd
    from core.research.adjustment.normalization import normalize_twse_closing_table
    from core.research.sources.twse import find_closing_table
    from datetime import datetime, timezone
    from core.backtest.execution import limit_prices
    sessions = sorted(d for d, (t, *_) in days.items() if t and d <= pd.Timestamp(day))
    stamp = datetime.now(timezone.utc)
    today, prev = (normalize_twse_closing_table(find_closing_table(days[d][2]), d, stamp).set_index('stock_id') for d in sessions[-1:] + sessions[-2:-1])
    reference = prev.raw_close.reindex(today.index)
    # ponytail: previous close as the reference; on an ex-rights date TWSE uses its ex-reference price, so the limit can be off that day
    up, down = limit_prices(reference.to_numpy(dtype=float))
    bars = {s: {'open': r.raw_open, 'high': r.raw_high, 'low': r.raw_low, 'close': r.raw_close, 'volume': r.volume, 'amount': r.amount, 'prior_amount': p,
                'limit_up': u, 'limit_down': dn} for (s, r), u, dn, p in zip(today.iterrows(), up, down, prev.amount.reindex(today.index))}
    return bars, {s: b['close'] for s, b in bars.items() if b['close'] == b['close']}
