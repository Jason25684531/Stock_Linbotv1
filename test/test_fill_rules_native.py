"""simulate_target_weights with bars: rejected orders never enter holdings or P&L; without bars the old behaviour is bit-identical."""
import numpy as np
import pandas as pd
import pytest

from core.research.fundamental_strategy_validation import simulate_target_weights

D = [pd.Timestamp(f'2024-01-0{i}') for i in range(2, 7)]
COST = {'fee_rate': 0.001425, 'tax_rate': 0.003, 'slippage': 0.0}


def targets(rows):
    return pd.DataFrame([{'strategy_id': 'T', 'asof_date': d - pd.Timedelta(days=1), 'execution_date': d, 'asset_id': a, 'target_weight': w} for d, a, w in rows])


def prices(**cols):
    return pd.DataFrame(cols, index=D)


def bar(o, amount=1e9, locked=None, prior_amount=1e9):
    high, low = (o, o) if locked else (o + 1, o - 1)
    return {'open': o, 'high': high, 'low': low, 'close': o, 'volume': 1000, 'amount': amount, 'prior_amount': prior_amount, 'limit_up': o if locked == 'up' else o + 10, 'limit_down': o if locked == 'down' else o - 10}


def test_no_bars_matches_old_behaviour_exactly():
    t = targets([(D[0], 'A', 0.5), (D[2], 'B', 0.5)])
    p = prices(A=[100, 101, 102, np.nan, 104], B=[50, 51, 52, 53, 54])
    old = simulate_target_weights(t, p, initial_capital=100_000, cost=COST)
    # baseline replayed on the pre-change module (git HEAD 6e69c9f) on 2026-10-26
    assert [(x['asset_id'], x['side'], x['shares']) for x in old['trades']] == [('A', 'buy', 500.0), ('A', 'sell', 500.0), ('B', 'buy', 970.0)]
    np.testing.assert_allclose(old['equity'].to_numpy(), [99928.75, 100428.75, 100631.198, 101601.198, 102571.198], rtol=1e-9, atol=1e-12)
    assert old['execution_log'].empty


def test_locked_limit_up_buy_is_rejected_cash_kept():
    t = targets([(D[0], 'A', 0.5)])
    p = prices(A=[100, 101, 102, 103, 104])
    r = simulate_target_weights(t, p, initial_capital=100_000, cost=COST, bars={D[0]: {'A': bar(100, locked='up')}})
    assert r['trades'] == [] and r['positions'] == {}
    assert r['execution_log'].iloc[0].to_dict() == {'execution_date': D[0], 'asset_id': 'A', 'side': 'buy', 'status': 'REJECTED', 'reason': 'REJECTED_LOCKED_LIMIT_UP'}
    assert r['equity'].iloc[-1] == 100_000  # no P&L from a trade that could not happen


@pytest.mark.parametrize('b,reason', [(None, 'REJECTED_NO_BAR'), ({**bar(100), 'volume': 0}, 'REJECTED_NO_TRADE')])
def test_no_bar_and_no_trade_buy(b, reason):
    t = targets([(D[0], 'A', 0.5)])
    r = simulate_target_weights(t, prices(A=[100] * 5), initial_capital=100_000, cost=COST, bars={D[0]: {'A': b} if b else {}})
    assert not r['trades'] and r['execution_log'].reason.tolist() == [reason]


def test_locked_limit_down_sell_keeps_position_and_retries():
    t = targets([(D[0], 'A', 0.5), (D[1], 'A', 0.0), (D[2], 'A', 0.0)])
    p = prices(A=[100, 90, 95, 96, 97])
    bars = {D[0]: {'A': bar(100)}, D[1]: {'A': bar(90, locked='down')}, D[2]: {'A': bar(95)}}
    r = simulate_target_weights(t, p, initial_capital=100_000, cost=COST, bars=bars)
    assert [(x['execution_date'], x['side'], x['price']) for x in r['trades']] == [(D[0], 'buy', 100.0), (D[2], 'sell', 95.0)]
    assert r['execution_log'].reason.tolist() == ['REJECTED_LOCKED_LIMIT_DOWN']


def test_participation_cap_partial_buy_and_partial_sell():
    t = targets([(D[0], 'A', 0.5), (D[2], 'A', 0.0)])
    p = prices(A=[100] * 5)
    r = simulate_target_weights(t, p, initial_capital=100_000, cost=COST, bars={D[0]: {'A': bar(100, prior_amount=200_000)}, D[2]: {'A': bar(100, prior_amount=100_000)}})
    assert r['trades'][0]['shares'] == 100  # 5 % of 200k / 100
    assert r['trades'][1]['shares'] == 50 and r['positions']['A']['shares'] == 50
    assert r['execution_log'].reason.tolist() == ['PARTICIPATION_CAP_OR_CASH', 'PARTICIPATION_CAP']  # both partial fills are logged


def test_cash_never_negative_and_no_short():
    t = targets([(D[0], 'A', 1.0), (D[0], 'B', 1.0)])
    r = simulate_target_weights(t, prices(A=[100] * 5, B=[100] * 5), initial_capital=10_000, cost=COST, bars={D[0]: {'A': bar(100), 'B': bar(100)}})
    cash = 10_000 - sum(x['notional'] + x['cost'] for x in r['trades'])
    assert cash >= 0 and all(x['shares'] > 0 for x in r['trades'])
    assert 'REJECTED_CAP_OR_CASH' in r['execution_log'].reason.tolist()


def test_vectorbt_mask_drops_the_instruction_and_is_diagnostic_only():
    from core.research.vectorbt_adapter import run_vectorbt
    dates = pd.date_range('2023-01-02', periods=6, freq='D')
    close = pd.DataFrame({'A': 10.0, 'B': 20.0}, index=dates)
    t = pd.DataFrame([{'asof_date': dates[0], 'execution_date': dates[1], 'asset_id': a, 'target_weight': 0.5, 'config_id': 'T'} for a in 'AB'])
    base = run_vectorbt(close, t, fee_rate=0.0, tax_rate=0.0, sparse_rebalance=True)
    masked = run_vectorbt(close, t, fee_rate=0.0, tax_rate=0.0, sparse_rebalance=True, fillable=pd.DataFrame({'A': [False], 'B': [True]}, index=[dates[1]]))
    partial = run_vectorbt(close, t, fee_rate=0.0, tax_rate=0.0, sparse_rebalance=True, fillable=pd.DataFrame({'A': [True]}, index=[dates[1]]))
    all_true = run_vectorbt(close, t, fee_rate=0.0, tax_rate=0.0, sparse_rebalance=True, fillable=pd.DataFrame({'A': [True], 'B': [True]}, index=[dates[1]]))
    assert base['fill_rule'] == 'ASSUMED_FILLED' and masked['fill_rule'] == 'DIAGNOSTIC_ONLY_MASK'
    assert set(base['orders'].Column) == {'A', 'B'} and set(masked['orders'].Column) == {'B'}
    assert set(partial['orders'].Column) == {'A'}  # a cell missing from the mask is not fillable (fail closed)
    pd.testing.assert_frame_equal(all_true['instruction_matrix'], base['instruction_matrix'])


def test_old_rule_filled_the_locked_buy_corrected_rule_does_not():
    t = targets([(D[0], 'A', 0.5)])
    p = prices(A=[100, 110, 120, 130, 140])
    old = simulate_target_weights(t, p, initial_capital=100_000, cost=COST)
    new = simulate_target_weights(t, p, initial_capital=100_000, cost=COST, bars={D[0]: {'A': bar(100, locked='up')}})
    assert old['equity'].iloc[-1] > 100_000 and new['equity'].iloc[-1] == 100_000
