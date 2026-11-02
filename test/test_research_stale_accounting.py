import copy

import pytest

from core.backtest.costs import CostModel
from core.research.repair_accounting import DataBlocked, reconcile, simulate


def test_suspension_uses_last_close_and_independent_audit():
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07']
    bar = {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000,
           'amount': 100000, 'prior_amount': 100000, 'limit_up': 11, 'limit_down': 9}
    # days[1] suspended; days[2] resumes but has no previous-session cap base, so the fill lands on days[3]
    bars = {days[0]: {'2330': bar}, days[1]: {}, days[2]: {'2330': bar}, days[3]: {'2330': bar}}
    orders = [dict(id='buy', date=days[0], stock_id='2330', side='buy', shares=10,
                   signal_at='2024-12-31T14:00:00+08:00', latest_input_available_at='2024-12-31T12:00:00+08:00'),
              dict(id='sell', date=days[1], stock_id='2330', side='sell', shares=10,
                   signal_at='2025-01-02T14:00:00+08:00', latest_input_available_at='2025-01-02T12:00:00+08:00')]
    data = dict(bars=bars, orders=orders, actions=[], action_coverage={'2330': days}, initial_cash=10000,
                cost=CostModel(.001, .003, 0), slippage=0)
    result = simulate(**data)
    assert result['daily'][1]['stale_marks'] == {'2330': days[0]}
    assert result['daily'][1]['marks'] == {'2330': 10}
    statuses = [event for event in result['ledger'] if event['order_id'] == 'sell']
    assert [(event['date'], event['status'], event['reason']) for event in statuses if event['kind'] == 'ORDER_STATUS'] == [
        (days[1], 'UNFILLED', 'REJECTED_NO_BAR'), (days[2], 'UNFILLED', 'REJECTED_PRIOR_AMOUNT_UNVERIFIABLE'), (days[3], 'FILLED', None)]
    assert reconcile(result=result, **data)['status'] == 'PASS'
    tampered = copy.deepcopy(result)
    tampered['daily'][1]['stale_marks']['2330'] = days[1]
    assert reconcile(result=tampered, **data)['reason'] == 'STALE_MARK_EVIDENCE_MISMATCH'
    data['actions'] = [dict(stock_id='2330', ex_date=days[1], type='cash', cash_per_share=1, stock_ratio=0,
                            cash_pay_date=days[2], stock_credit_date=None, source='fixture', source_sha256='a' * 64)]
    with pytest.raises(DataBlocked, match='OFFICIAL_MARK_MISSING'):
        simulate(**data)
