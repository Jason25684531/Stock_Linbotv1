"""Same bars, same order -> the native backtest (simulate_target_weights with bars) and the paper broker agree per order.
Three sessions: D0 buy, D1 sell attempt, D2 sell attempt again (a sell rejected on D1 is retried on D2 in both engines)."""
import pandas as pd
import pytest

from core.research.fundamental_strategy_validation import simulate_target_weights
from scripts import paper_trading as pt

DAYS = [pd.Timestamp('2024-03-04'), pd.Timestamp('2024-03-05'), pd.Timestamp('2024-03-06')]
D0, D1, D2 = DAYS
CASH = 1_000_000.0
COST = {'fee_rate': pt.COSTS.fee_rate, 'tax_rate': pt.COSTS.tax_rate, 'slippage': pt.SLIPPAGE, 'minimum_fee': pt.COSTS.minimum_fee}  # same CostModel as the broker
TOL = dict(rel=1e-9, abs=1e-12)


def bar(o=100.0, locked=None, volume=1000, amount=1e9, prior_amount=1e9):
    high, low = (o, o) if locked else (o + 1, o - 1)
    return {'open': o, 'high': high, 'low': low, 'close': o, 'volume': volume, 'amount': amount, 'prior_amount': prior_amount, 'limit_up': o if locked == 'up' else o + 10, 'limit_down': o if locked == 'down' else o - 10}


def native(bars, weight):
    """bars: [D0 buy bar, D1 sell bar, D2 sell bar]; returns {(date, side): (shares, price, cost)} fills, {(date, side): reason} rejections, positions."""
    targets = pd.DataFrame([{'strategy_id': 'T', 'asof_date': d - pd.Timedelta(days=1), 'execution_date': d, 'asset_id': 'A', 'target_weight': w}
                            for d, w in zip(DAYS, (weight, 0.0, 0.0))])
    prices = pd.DataFrame({'A': [100.0] * 3}, index=DAYS)
    r = simulate_target_weights(targets, prices, initial_capital=CASH, cost=COST, bars={d: ({'A': b} if b else {}) for d, b in zip(DAYS, bars)})
    log = r['execution_log']
    partial = {(x.execution_date, x.side) for x in log.itertuples() if x.status == 'PARTIAL'} if len(log) else set()
    fills = {(t['execution_date'], t['side']): (int(t['shares']), t['price'], t['cost'], 'PARTIAL' if (t['execution_date'], t['side']) in partial else 'FILLED') for t in r['trades']}
    rejects = {(x.execution_date, x.side): x.reason for x in log.itertuples() if x.status != 'PARTIAL'} if len(log) else {}
    return fills, rejects, {k: int(v['shares']) for k, v in r['positions'].items()}


def paper(root, bars, shares):
    """Same three sessions on the paper broker; the sell order is for every share held, replanned after a rejection like the native engine."""
    pt.open_account(root, CASH, 'T')
    out = {}
    for i, (day, b) in enumerate(zip(('D0', 'D1', 'D2'), bars)):
        state = pt.load(root)
        held = state['positions'].get('A', 0)
        state['pending'] = [{'stock_id': 'A', 'side': 'buy', 'shares': shares, 'plan_date': 'P'}] if i == 0 else \
            ([{'stock_id': 'A', 'side': 'sell', 'shares': held, 'plan_date': 'P'}] if held else [])
        pt._save(root / 'state.json', state)
        for o in pt.execute(root, day, {'A': b} if b else {}):
            out[(DAYS[i], o['side'])] = o
        assert pt.reconcile(root)['status'] == 'PASS'
    return out, pt.load(root)


SCENARIOS = {
    'normal': [bar(), bar(), bar()],
    'locked_up_buy': [bar(locked='up'), bar(), bar()],
    'locked_down_sell_then_resumed': [bar(), bar(locked='down'), bar()],
    'no_bar_buy': [None, bar(), bar()],
    'zero_volume_buy': [bar(volume=0), bar(), bar()],
    'cap_partial_buy': [bar(prior_amount=200_000.0), bar(), bar()],
    'cap_partial_sell_then_rest': [bar(), bar(prior_amount=100_000.0), bar()],
    'suspended_then_resumed': [bar(), None, bar()],
    'cash_short': [bar(), bar(), bar()],  # weight 1.0: the cash constraint, not the cap, sizes the buy in both engines
}


@pytest.mark.parametrize('name', list(SCENARIOS))
def test_per_order_parity(tmp_path, name):
    weight = 1.0 if name == 'cash_short' else 0.5
    fills, rejects, positions = native(SCENARIOS[name], weight)
    shares = int(CASH * weight // (100.0 * (1 + pt.SLIPPAGE)))  # the native engine's initial size, before cash and cap reductions
    orders, state = paper(tmp_path, SCENARIOS[name], shares)
    for key in sorted(set(fills) | set(rejects) | set(orders)):
        o = orders.get(key)
        assert o is not None, f'{key}: native acted, paper has no order'
        if key in fills:
            n, price, cost, status = fills[key]
            assert o['status'] == status and o['filled_shares'] == n, (key, o, status)
            assert o['price'] == pytest.approx(price, **TOL) and o['cost'] == pytest.approx(cost, **TOL)
        else:
            assert o['status'] == rejects[key] and o['filled_shares'] == 0, (key, o, rejects)
    assert state['positions'] == positions and state['cash'] >= 0 and all(v > 0 for v in state['positions'].values())
    if name == 'cash_short':
        n, price, cost, status = fills[(D0, 'buy')]
        assert status == 'PARTIAL' and n < shares and n * price + cost <= CASH < (n + 1) * price + cost  # the largest affordable size, in both engines


def test_without_minimum_fee_the_research_cost_is_the_old_unrounded_one():
    from core.research.fundamental_strategy_validation import _cost_for
    assert _cost_for(100_000, 'buy', {'fee_rate': 0.001425}) == 142.5
    assert _cost_for(100_000, 'buy', {'fee_rate': 0.001425, 'minimum_fee': 20}) == 142 == pt.COSTS.buy_cost(100_000)
