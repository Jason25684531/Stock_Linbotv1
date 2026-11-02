import copy

import pytest

from core.backtest.costs import CostModel
from core.research.repair_accounting import DataBlocked, reconcile, registration, simulate

DAYS = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08']


def bar(price=10, prior=100_000, amount=100_000):
    return {'open': price, 'high': price + 1, 'low': price - 1, 'close': price, 'volume': 10_000,
            'amount': amount, 'prior_amount': prior, 'limit_up': price + 1, 'limit_down': price - 1}


def example(multiple=1):
    bars = {day: {'2330': bar(10 if i == 0 else 9)} for i, day in enumerate(DAYS)}
    orders = [{'id': 'b', 'date': DAYS[0], 'stock_id': '2330', 'side': 'buy', 'shares': 100,
               'signal_at': '2024-12-31T14:00:00+08:00', 'latest_input_available_at': '2024-12-31T12:00:00+08:00'},
              {'id': 's', 'date': DAYS[2], 'stock_id': '2330', 'side': 'sell', 'shares': 40,
               'signal_at': '2025-01-03T14:00:00+08:00', 'latest_input_available_at': '2025-01-03T12:00:00+08:00'}]
    actions = [{'stock_id': '2330', 'ex_date': DAYS[1], 'type': 'cash_stock', 'cash_per_share': .5,
                'stock_ratio': .1, 'cash_pay_date': DAYS[3], 'stock_credit_date': DAYS[4],
                'source': 'synthetic_fixture', 'source_sha256': 'a' * 64}]
    return {'bars': bars, 'orders': orders, 'actions': actions, 'action_coverage': {'2330': DAYS}, 'initial_cash': 10_000,
            'cost': CostModel(.01 * multiple, .02 * multiple, 0), 'slippage': 0}


def check(data, result, bars=None):
    return reconcile(10_000, result, bars=bars or data['bars'], orders=data['orders'], actions=data['actions'],
                     action_coverage=data['action_coverage'], cost=data['cost'], slippage=data['slippage'])


@pytest.mark.parametrize('multiple,expected_cash', [(1, [8990, 8990, 9340, 9390, 9390]),
                                                   (2, [8980, 8980, 9319, 9369, 9369])])
def test_mixed_dividend_independent_daily_cash_holdings_and_nav(multiple, expected_cash):
    data = example(multiple)
    result = simulate(**data)
    assert result['data_status'] == 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS'
    assert check(data, result) == {'status': 'PASS', 'days': 5}
    assert [r['cash'] for r in result['daily']] == expected_cash
    assert [r['positions'].get('2330', 0) for r in result['daily']] == [100, 100, 60, 60, 70]
    assert [r['cash_receivable'] for r in result['daily']] == [0, 50, 50, 0, 0]
    assert [sum(s['shares'] for s in r['stock_receivable']) for r in result['daily']] == [0, 10, 10, 10, 0]
    expected_nav = [expected_cash[0] + 1000, expected_cash[1] + 900 + 50 + 90,
                    expected_cash[2] + 540 + 50 + 90, expected_cash[3] + 540 + 90, expected_cash[4] + 630]
    assert [r['nav'] for r in result['daily']] == expected_nav
    fills = [e for e in result['ledger'] if e['kind'] == 'FILL']
    assert fills[0]['tax'] == 0 and fills[1]['tax'] == int(360 * .02 * multiple)
    assert fills[0]['fee'] == int(1000 * .01 * multiple) and fills[1]['fee'] == int(360 * .01 * multiple)
    tampered = copy.deepcopy(result)
    tampered['daily'][2]['nav'] += 1
    assert check(data, tampered).items() >= {'status': 'FAIL', 'date': DAYS[2], 'reason': 'STATE_MISMATCH'}.items()


def test_partial_fills_share_prior_liquidity_and_never_use_same_day_amount():
    data = example()
    data['actions'] = []
    data['orders'] = [{**data['orders'][0], 'id': 'a', 'shares': 80}, {**data['orders'][0], 'id': 'b', 'shares': 80}]
    data['bars'] = {day: {'2330': bar(prior=20_000, amount=20_000)} for day in DAYS}
    first = simulate(**data)
    # Same-day value is huge but must not be used; the next session's prior follows it so the market stays consistent.
    data['bars'][DAYS[0]]['2330']['amount'] = 1_000_000_000
    data['bars'][DAYS[1]]['2330']['prior_amount'] = 1_000_000_000
    second = simulate(**data)
    day1 = [e for e in first['ledger'] if e['kind'] == 'ORDER_STATUS' and e['date'] == DAYS[0]]
    assert [(e['filled'], e['remaining']) for e in day1] == [(80, 0), (20, 60)]
    assert first['ledger'] == second['ledger']
    assert first['daily'][1]['positions']['2330'] == 160 and not first['pending_orders']
    assert check(data, second)['status'] == 'PASS'
    data['bars'][DAYS[1]]['2330']['prior_amount'] = 5  # caller-supplied cap base must match the previous session's amount
    assert check(data, second) == {'status': 'FAIL', 'date': DAYS[1], 'reason': 'PRIOR_AMOUNT_MISMATCH'}
    data['bars'][DAYS[1]]['2330']['prior_amount'] = 1_000_000_000
    del data['bars'][DAYS[0]]['2330']  # resumption after a session without a bar: no cap base, no fill
    data['action_coverage']['2330'] = DAYS
    resumed = simulate(**data)
    assert [e['reason'] for e in resumed['ledger'] if e['kind'] == 'ORDER_STATUS' and e['date'] == DAYS[1]] == ['REJECTED_PRIOR_AMOUNT_UNVERIFIABLE'] * 2
    assert check(data, resumed)['status'] == 'PASS'


def test_unfilled_locked_order_retries_next_session():
    data = example()
    data['actions'] = []
    data['orders'] = data['orders'][:1]
    locked = data['bars'][DAYS[0]]['2330']
    locked.update(open=11, high=11, low=11, close=11, limit_up=11)
    result = simulate(**data)
    statuses = [e for e in result['ledger'] if e['kind'] == 'ORDER_STATUS']
    assert statuses[0]['status'] == 'UNFILLED' and statuses[0]['reason'] == 'REJECTED_LOCKED_LIMIT_UP'
    assert statuses[1]['status'] == 'FILLED' and result['daily'][1]['positions']['2330'] == 100


def test_missing_terms_unknown_coverage_and_fractional_shares_fail_closed():
    data = example()
    data['actions'][0]['cash_pay_date'] = None
    with pytest.raises(DataBlocked):
        simulate(**data)
    data = example()
    data['actions'][0]['type'] = 'rights_issue'
    with pytest.raises(DataBlocked, match='UNSUPPORTED'):
        simulate(**data)
    data = example()
    data['actions'][0]['stock_ratio'] = .015
    with pytest.raises(DataBlocked, match='FRACTIONAL'):
        simulate(**data)
    data = example()
    data['action_coverage']['2330'] = [DAYS[0]]
    with pytest.raises(DataBlocked, match='ACTION_COVERAGE_UNKNOWN'):
        simulate(**data)


def test_fractional_drop_is_explicit_and_reconciled():
    data = example()
    data['actions'][0]['stock_ratio'] = .015
    result = simulate(**data, fractional_policy='DROP')
    event = next(e for e in result['ledger'] if e['kind'] == 'FRACTIONAL_DROPPED')
    assert event['shares'] == .5 and event['unmodeled_fractional_value'] == 4.5
    kwargs = dict(bars=data['bars'], orders=data['orders'], actions=data['actions'],
                  action_coverage=data['action_coverage'], cost=data['cost'], slippage=data['slippage'])
    assert reconcile(10_000, result, fractional_policy='DROP', **kwargs)['status'] == 'PASS'
    event['unmodeled_fractional_value'] = 0
    assert reconcile(10_000, result, fractional_policy='DROP', **kwargs)['reason'] == 'FRACTIONAL_DROPPED_MISMATCH'


def test_no_false_coverage_after_selling_all_and_missing_held_close_blocks():
    data = example()
    data['actions'] = []
    data['orders'][1]['shares'] = 100
    data['action_coverage']['2330'] = DAYS[:2]
    with pytest.raises(DataBlocked, match='ACTION_COVERAGE_UNKNOWN'):
        simulate(**data)
    data = example()
    data['bars'][DAYS[1]]['2330']['close'] = float('nan')
    with pytest.raises(DataBlocked, match='INVALID_CLOSE'):
        simulate(**data)


def test_order_lookahead_oos_and_tampered_price_fail_closed():
    data = example()
    data['orders'][0]['latest_input_available_at'] = '2025-01-02T10:00:00+08:00'
    with pytest.raises(DataBlocked, match='ORDER_LOOKAHEAD_BLOCKED'):
        simulate(**data)
    data = example()
    data['orders'][0]['signal_at'] = '2025-01-02T00:30:00+08:00'
    with pytest.raises(DataBlocked, match='ORDER_LOOKAHEAD_BLOCKED'):
        simulate(**data)
    data = example()
    data['bars']['2026-01-02'] = data['bars'][DAYS[-1]]
    with pytest.raises(DataBlocked, match='OOS_BLOCKED'):
        simulate(**data)
    data = example()
    result = simulate(**data)
    changed_bars = copy.deepcopy(data['bars'])
    changed_bars[DAYS[1]]['2330']['close'] += 1
    assert check(data, result, bars=changed_bars) == {'status': 'FAIL', 'date': DAYS[1], 'reason': 'MARK_MISMATCH'}
    bad_cost = copy.deepcopy(result)
    next(e for e in bad_cost['ledger'] if e['kind'] == 'FILL')['fee'] += 1
    assert check(data, bad_cost) == {'status': 'FAIL', 'date': DAYS[0], 'reason': 'FILL_OR_COST_MISMATCH'}
    bad_dividend = copy.deepcopy(result)
    next(e for e in bad_dividend['ledger'] if e['kind'] == 'CASH_ENTITLEMENT')['amount'] += 1
    assert check(data, bad_dividend) == {'status': 'FAIL', 'date': DAYS[1], 'reason': 'ENTITLEMENT_MISMATCH'}
    early_credit = copy.deepcopy(result)
    next(e for e in early_credit['ledger'] if e['kind'] == 'CASH_CREDIT')['date'] = DAYS[2]
    assert check(data, early_credit)['status'] == 'FAIL'
    bad_stock_date = copy.deepcopy(result)
    bad_stock_date['daily'][1]['stock_receivable'][0]['date'] = DAYS[3]
    assert check(data, bad_stock_date) == {'status': 'FAIL', 'date': DAYS[1], 'reason': 'RECEIVABLE_CLAIM_MISMATCH'}
    bad_cash_date = copy.deepcopy(result)
    bad_cash_date['daily'][1]['cash_receivable_claims'][0]['date'] = DAYS[2]
    assert check(data, bad_cash_date) == {'status': 'FAIL', 'date': DAYS[1], 'reason': 'RECEIVABLE_CLAIM_MISMATCH'}
    bad_order = copy.deepcopy(result)
    bad_order['ledger'] = [e for e in bad_order['ledger'] if not (e['kind'] == 'ORDER_STATUS' and e['date'] == DAYS[0])]
    assert check(data, bad_order) == {'status': 'FAIL', 'date': DAYS[0], 'reason': 'ORDER_COUNT_MISMATCH'}


def test_accounting_is_research_only(monkeypatch):
    from core.runtime import operation_policy as policy
    monkeypatch.setattr(policy, 'current_mode', lambda: policy.ExecutionMode.PRODUCTION)
    with pytest.raises(DataBlocked, match='RESEARCH_MODE_REQUIRED'):
        simulate(**example())


def test_a191_repair_registration_preserves_fixed_scope_and_uses_existing_registry(monkeypatch):
    from scripts import research_workflow as workflow
    captured = []
    monkeypatch.setattr(workflow, 'register', lambda row: captured.append(row) or row)
    record = workflow.cmd_register_repair_a191()
    assert record == registration() == captured[0]
    assert record['strategy'] == 'A191_132' and record['trial_budget'] == '12'
    assert 'unchanged g1-g5' in record['falsification_test'] and 'no 2026 OOS' in record['data_window']


def test_a191_repair_registration_rejects_production(monkeypatch):
    from core.runtime import operation_policy as policy
    from scripts import research_workflow as workflow
    monkeypatch.setattr(policy, 'current_mode', lambda: policy.ExecutionMode.PRODUCTION)
    with pytest.raises(PermissionError, match='RESEARCH_MODE_REQUIRED'):
        workflow.cmd_register_repair_a191()


def test_slippage_and_minimum_fee_are_verified_against_separate_inputs():
    data = example()
    data['slippage'] = .01
    data['cost'] = CostModel(.001, .003, 20)
    result = simulate(**data)
    fills = [e for e in result['ledger'] if e['kind'] == 'FILL']
    assert fills[0]['price'] == pytest.approx(10.1) and fills[0]['fee'] == 20 and fills[0]['tax'] == 0
    assert fills[1]['price'] == pytest.approx(8.91) and fills[1]['fee'] == 20 and fills[1]['tax'] == 1
    assert check(data, result)['status'] == 'PASS'


def test_reconciler_rejects_missing_expected_fill_and_wrong_stock():
    data = example()
    data['actions'] = []
    data['orders'] = data['orders'][:1]
    no_fill = copy.deepcopy(data)
    for bar_map in no_fill['bars'].values():
        bar_map['2330']['prior_amount'] = 1
    assert check(data, simulate(**no_fill))['status'] == 'FAIL'
    for day in DAYS:
        data['bars'][day]['2317'] = copy.deepcopy(data['bars'][day]['2330'])
    data['action_coverage']['2317'] = DAYS
    wrong_stock = copy.deepcopy(data)
    wrong_stock['orders'][0]['stock_id'] = '2317'
    forged = simulate(**wrong_stock)
    next(e for e in forged['ledger'] if e['kind'] == 'ORDER_STATUS')['stock_id'] = '2330'
    assert check(data, forged)['status'] == 'FAIL'


def test_reconciler_rejects_orphan_extra_fill():
    data = example()
    data['actions'] = []
    data['orders'] = data['orders'][:1]
    for day in DAYS:
        data['bars'][day]['2317'] = copy.deepcopy(data['bars'][day]['2330'])
    data['action_coverage']['2317'] = DAYS
    extra = copy.deepcopy(data)
    extra['orders'].append({**data['orders'][0], 'id': 'extra', 'stock_id': '2317'})
    forged = simulate(**extra)
    forged['ledger'] = [e for e in forged['ledger'] if not (e['kind'] == 'ORDER_STATUS' and e['order_id'] == 'extra')]
    assert check(data, forged)['status'] == 'FAIL'


def test_multiple_cash_dividends_for_same_stock_on_same_pay_date_reconcile():
    data = example()
    data['orders'] = data['orders'][:1]
    first = {**data['actions'][0], 'type': 'cash', 'cash_per_share': .5, 'stock_ratio': 0,
             'cash_pay_date': DAYS[-1], 'stock_credit_date': None}
    second = {**first, 'ex_date': DAYS[2], 'cash_per_share': .7}
    data['actions'] = [first, second]
    result = simulate(**data)
    assert result['daily'][2]['cash_receivable'] == 120
    assert result['daily'][-1]['cash_receivable'] == 0
    assert check(data, result) == {'status': 'PASS', 'days': len(DAYS)}
