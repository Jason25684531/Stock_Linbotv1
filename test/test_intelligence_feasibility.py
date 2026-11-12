import pandas as pd
import pytest

from core.backtest.costs import CostModel
from core.research.intelligence import feasibility as fz

COSTS = {'fee_rate': 0.001425, 'tax_rate': 0.003, 'minimum_fee': 20}
CM = CostModel(0.001425, 0.003, 20)
DAYS = pd.bdate_range('2020-01-01', periods=6)
D = [d.strftime('%Y-%m-%d') for d in DAYS]


def _bars():
    rows = []
    for i, d in enumerate(DAYS):
        rows.append({'trade_date': d, 'stock_id': '1101', 'high': 11.0, 'low': 9.0, 'close': 10.0 + i, 'volume': 1e6, 'amount': 1e8, 'status': 'TRADED'})
        rows.append({'trade_date': d, 'stock_id': '2330', 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1e6, 'amount': 2e8, 'status': 'TRADED'})
    return pd.DataFrame(rows)


def _pct(shares, buy_price, net_total):
    basis = shares * buy_price + CM.buy_cost(shares * buy_price)
    return (net_total - basis) / basis * 100


def _engine_nav(cash_flow_days, holdings_by_day, initial=1_000_000.0):
    """NAV the way the engine marks it: cash after the session's fills plus shares x close."""
    closes = {('1101', d): 10.0 + i for i, d in enumerate(D)} | {('2330', d): 100.0 for d in D}
    cash, nav = initial, []
    for d in D:
        cash += cash_flow_days.get(d, 0.0)
        nav.append(cash + sum(s * closes[(sid, d)] for sid, s in holdings_by_day.get(d, {}).items()))
    return pd.DataFrame({'date': D, 'nav_base': nav, 'nav_stress': nav})


def test_exact_rebuild_reconciles():
    buy, sell = 1000 * 10.5, 1000 * 13.2
    flows = {D[1]: -(buy + CM.buy_cost(buy)), D[4]: sell - CM.sell_cost(sell)}
    holdings = {D[1]: {'1101': 1000}, D[2]: {'1101': 1000}, D[3]: {'1101': 1000}}
    nav = _engine_nav(flows, holdings)
    trades = pd.DataFrame({'stock_id': ['1101'], 'buy_date': [D[1]], 'buy_price': [10.5], 'sell_date': [D[4]], 'sell_price': [13.2],
                           'shares': [1000], 'exit_shares': [1000], 'profit_pct': [_pct(1000, 10.5, sell - CM.sell_cost(sell))], 'distributions': [0.0]})
    out = fz.rebuild(nav, trades, None, [], _bars(), COSTS, 1_000_000.0)
    assert out['status'] == 'RECONCILED', out
    day = out['daily'][2]
    assert day['holdings'] == ['1101'] and day['max_weight'] == pytest.approx(1000 * 12 / nav.nav_base[2])


def test_corporate_action_and_open_position():
    buy = 1000 * 10.5
    flows = {D[1]: -(buy + CM.buy_cost(buy)), D[3]: 500.0}
    holdings = {D[1]: {'1101': 1000}, D[2]: {'1101': 1000}, D[3]: {'1101': 1100}, D[4]: {'1101': 1100}, D[5]: {'1101': 1100}}
    nav = _engine_nav(flows, holdings)
    actions = pd.DataFrame({'date': [D[3]], 'stock_id': ['1101'], 'shares_before': [1000], 'shares_after': [1100], 'cash_credited': [500.0],
                            'price_factor': [0.9], 'status': ['BOOKED']})
    opened = [{'stock_id': '1101', 'shares': 1100, 'entry_price': 10.5, 'entry_value': 1100 * 10.5 * 0.9, 'buy_date': D[1]}]  # entry_value is not the raw notional here
    empty = pd.DataFrame(columns=['stock_id', 'buy_date', 'buy_price', 'sell_date', 'sell_price', 'shares', 'exit_shares', 'profit_pct'])
    out = fz.rebuild(nav, empty, actions, opened, _bars(), COSTS, 1_000_000.0)
    assert out['status'] == 'RECONCILED', out


def test_partial_exit_is_unverifiable_with_the_first_date():
    buy, part, rest = 1000 * 10.5, 400 * 12.1, 600 * 13.2
    flows = {D[1]: -(buy + CM.buy_cost(buy)), D[3]: part - CM.sell_cost(part), D[4]: rest - CM.sell_cost(rest)}
    holdings = {D[1]: {'1101': 1000}, D[2]: {'1101': 1000}, D[3]: {'1101': 600}}
    nav = _engine_nav(flows, holdings)
    trades = pd.DataFrame({'stock_id': ['1101'], 'buy_date': [D[1]], 'buy_price': [10.5], 'sell_date': [D[4]], 'sell_price': [13.2],
                           'shares': [1000], 'exit_shares': [600], 'profit_pct': [_pct(1000, 10.5, part - CM.sell_cost(part) + rest - CM.sell_cost(rest))],
                           'distributions': [0.0]})
    out = fz.rebuild(nav, trades, None, [], _bars(), COSTS, 1_000_000.0)
    assert out['status'] == 'POSITIONS_UNVERIFIABLE'
    assert out['first_mismatch_date'] == D[3] and 'PARTIAL_EXITS_NOT_STORED:1' in out['reasons']
    assert out['mismatch_outside_partial_exit_windows'] == 0 and 'daily' not in out
    hidden = trades.assign(exit_shares=[float('nan')])  # runner.sell leaves exit_shares empty when the position had no corporate action
    out = fz.rebuild(nav, hidden, None, [], _bars(), COSTS, 1_000_000.0)
    assert out['status'] == 'POSITIONS_UNVERIFIABLE' and 'PARTIAL_EXITS_NOT_STORED:1' in out['reasons']


def test_liquidity_and_capacity_labels():
    trades = pd.DataFrame({'stock_id': ['1101'], 'buy_date': [D[1]], 'buy_price': [10.5], 'sell_date': [D[4]], 'sell_price': [13.2],
                           'shares': [1000], 'exit_shares': [1000]})
    liq = fz.liquidity(trades, _bars())
    assert liq['entry_participation_prev']['p90'] == pytest.approx(10500 / 1e8)
    cap = fz.capacity(liq, 1_000_000.0, reconciled=True)
    assert cap['label'] == 'CAPACITY_ESTIMATE_ASSUMED' and cap['value'] == pytest.approx(1_000_000.0 * 0.05 / (10500 / 1e8))
    assert fz.capacity(liq, 1_000_000.0, reconciled=False)['label'] == 'POSITIONS_UNVERIFIABLE'
    missing = fz.liquidity(trades, _bars()[_bars().trade_date > DAYS[1]])
    assert fz.capacity(missing, 1_000_000.0, reconciled=True)['label'] == 'CAPACITY_UNVERIFIED'
