"""The one fill rule (core.backtest.execution) and its reuse by the paper broker and the audit."""
import math

import pytest

from core.backtest.execution import CAP, cap_shares, fill_check, limit_prices
from scripts import paper_trading as pt
from scripts import track_b_evaluate as tb


def bar(open_=100.0, high=101.0, low=99.0, volume=1000, amount=1e6, up=110.0, down=90.0, prior_amount=1e6):
    return {'open': open_, 'high': high, 'low': low, 'close': open_, 'volume': volume, 'amount': amount, 'prior_amount': prior_amount,
            'limit_up': up, 'limit_down': down}


@pytest.mark.parametrize('side,b,expected', [
    ('buy', None, 'REJECTED_NO_BAR'),
    ('buy', bar(volume=0), 'REJECTED_NO_TRADE'),
    ('buy', bar(open_=math.nan), 'REJECTED_NO_TRADE'),
    ('sell', bar(amount=None), 'REJECTED_NO_TRADE'),
    ('buy', bar(110.0, 110.0, 110.0), 'REJECTED_LOCKED_LIMIT_UP'),
    ('sell', bar(110.0, 110.0, 110.0), None),  # locked up is fine for a sell
    ('sell', bar(90.0, 90.0, 90.0), 'REJECTED_LOCKED_LIMIT_DOWN'),
    ('buy', bar(90.0, 90.0, 90.0), None),
    ('buy', bar(100.0, 100.0, 100.0), None),  # flat but not at a limit
    ('buy', bar(), None),
    ('buy', {**bar(), 'limit_up': None}, 'REJECTED_NO_BAR'),  # unknown limit band
    ('buy', bar(amount=0), 'REJECTED_NO_TRADE'),
])
def test_fill_check(side, b, expected):
    assert fill_check(side, b) == expected


def test_cap_shares():
    assert cap_shares(1000, 100.0, 1e6) == int(CAP * 1e6 / 100.0) == 500
    assert cap_shares(100, 100.0, 1e6) == 100
    assert cap_shares(100, 100.0, None) == 0  # unknown prior traded value: never read as unlimited
    assert cap_shares(100, 100.0, float('nan')) == 0
    assert cap_shares(100, 100.0, 0.0) == 0  # known zero traded value: nothing can fill


def test_paper_broker_and_audit_use_the_shared_rule():
    assert pt.fill_check is fill_check and pt.cap_shares is cap_shares and pt.CAP == CAP
    assert tb.limit_prices is limit_prices
    up, down = limit_prices(__import__('numpy').array([100.0]))
    assert (up[0], down[0]) == (110.0, 90.0)


def test_same_day_amount_never_sizes_an_order(tmp_path):
    """No look-ahead: the native backtest and the paper broker fill the same shares whatever the session's own traded value
    (known only after the close); only prior_amount (the previous session's value) sizes the order. The day's value is a diagnostic."""
    import pandas as pd
    from core.research.fundamental_strategy_validation import simulate_target_weights
    day = pd.Timestamp('2024-03-04')
    targets = pd.DataFrame([{'strategy_id': 'T', 'asof_date': day - pd.Timedelta(days=1), 'execution_date': day, 'asset_id': 'A', 'target_weight': 0.5}])
    native, paper = [], []
    for same_day in (1.0, 1e12):
        b = bar(amount=same_day, prior_amount=200_000)
        r = simulate_target_weights(targets, pd.DataFrame({'A': [100.0]}, index=[day]), initial_capital=1_000_000, bars={day: {'A': b}})
        native.append(int(r['trades'][0]['shares']))
        assert r['fill_rule'] == 'CORRECTED_FILL_RULE' and r['trades'][0]['same_day_amount_share'] == pytest.approx(100 * 100 / same_day)
        root = tmp_path / str(same_day)
        pt.open_account(root, 1_000_000.0, 'T')
        state = pt.load(root)
        state['pending'] = [{'stock_id': 'A', 'side': 'buy', 'shares': 5000, 'plan_date': 'P'}]
        pt._save(root / 'state.json', state)
        paper.append(pt.execute(root, 'D1', {'A': b})[0]['filled_shares'])
    assert native == [100, 100] and paper[0] == paper[1] == int(CAP * 200_000 / (100 * (1 + pt.SLIPPAGE)))


def test_bars_without_prior_amount_report_diagnostic_only():
    import pandas as pd
    from core.research.fundamental_strategy_validation import simulate_target_weights
    day = pd.Timestamp('2024-03-04')
    targets = pd.DataFrame([{'strategy_id': 'T', 'asof_date': day - pd.Timedelta(days=1), 'execution_date': day, 'asset_id': 'A', 'target_weight': 0.5}])
    r = simulate_target_weights(targets, pd.DataFrame({'A': [100.0]}, index=[day]), bars={day: {'A': bar(prior_amount=None)}})
    assert r['fill_rule'] == 'DIAGNOSTIC_ONLY' and not r['trades']
