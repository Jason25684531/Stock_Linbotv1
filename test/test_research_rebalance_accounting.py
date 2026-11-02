import copy

import pytest

from core.backtest.costs import CostModel
from core.research.repair_accounting import DataBlocked, reconcile, simulate


def test_close_hook_replaces_pending_order_and_reconciles():
    days = ['2025-01-02', '2025-01-03']
    bar = {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000,
           'amount': 10000, 'prior_amount': 10000, 'limit_up': 11, 'limit_down': 9}
    bars = {day: {'A': dict(bar), 'B': dict(bar)} for day in days}
    first = dict(id='old', date=days[0], stock_id='A', side='buy', shares=80,
                 signal_at='2024-12-31T14:00:00+08:00', latest_input_available_at='2024-12-31T12:00:00+08:00')
    second = dict(id='new', date=days[1], stock_id='B', side='buy', shares=20,
                  signal_at='2025-01-02T14:00:00+08:00', latest_input_available_at='2025-01-02T13:30:00+08:00')
    def hook(day, pending):
        if day['date'] == days[0]:
            assert day['positions']['A'] == 50 and pending[0]['remaining'] == 30
            return {'cancel': ['old'], 'orders': [second]}
    data = dict(bars=bars, orders=[first], actions=[], action_coverage={'A': days, 'B': days}, initial_cash=10000,
                cost=CostModel(.001, .003, 0), slippage=0)
    result = simulate(**data, close_hook=hook)
    assert result['cancellations'] == [{'date': days[0], 'order_id': 'old'}]
    assert result['generated_orders'] == [second]
    assert result['daily'][1]['positions'] == {'A': 50, 'B': 20}
    kwargs = {**data, 'orders': [first, second], 'cancellations': result['cancellations']}
    assert reconcile(result=result, **kwargs)['status'] == 'PASS'
    changed = copy.deepcopy(result)
    next(e for e in changed['ledger'] if e['kind'] == 'CANCEL')['remaining'] = 10
    assert reconcile(result=changed, **kwargs)['reason'] == 'CANCELLATION_MISMATCH'
    with pytest.raises(DataBlocked, match='INVALID_CANCELLATION'):
        simulate(**data, close_hook=lambda day, pending: {'cancel': ['missing']})
