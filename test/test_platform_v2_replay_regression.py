from test_platform_v2_engine import engine


def test_replayed_partial_sell_does_not_change_accounting(monkeypatch):
    from core.backtest import runner

    monkeypatch.setattr(runner.Config, 'SLIPPAGE_MAX_PIPS', 0.0, raising=False)
    account = engine()
    account.buy('2330', 100.0, '2026-01-06')
    account._fill = {'checked': True, 'prior_amount': 2_200_100.0}
    account.sell('2330', 110.0, '2026-01-07', 'TEST')
    cash, shares, fills = account.capital, account.positions['2330']['shares'], account.fill_rows().copy()

    account.sell('2330', 110.0, '2026-01-07', 'TEST')

    assert account.capital == cash
    assert account.positions['2330']['shares'] == shares
    assert account.fill_rows() == fills


def test_nonfinite_nav_is_not_publishable():
    from core.research.platform.ledger_v2 import reconcile

    result = reconcile(100.0, [['2025-01-01', float('nan')]], [], [],
                       [{'date': '2025-01-01', 'stock_id': 'CASH', 'value': 100.0}], [])
    assert result['status'] == 'NOT_PUBLISHABLE'


def test_nan_weight_is_refused():
    import pytest
    from core.research.platform.portfolio_v2 import check_weights

    with pytest.raises(ValueError):
        check_weights(['a', 'b'], [float('nan'), 0.5])


def test_sell_without_position_cannot_be_published():
    from core.research.platform.ledger_v2 import reconcile
    fill = {'fill_id': 'f', 'date': '2025-01-01', 'side': 'SELL', 'stock_id': '2330', 'shares': 1, 'cash_delta': 10}
    positions = [{'date': '2025-01-01', 'stock_id': 'CASH', 'value': 110},
                 {'date': '2025-01-01', 'stock_id': '2330', 'shares': -1, 'value': -10}]
    assert reconcile(100, [['2025-01-01', 100]], [fill], [], positions,
                     [{'stock_id': '2330', 'shares': -1}])['status'] == 'NOT_PUBLISHABLE'


def test_duplicate_position_rows_cannot_be_published():
    from core.research.platform.ledger_v2 import reconcile
    rows = [{'date': '2025-01-01', 'stock_id': 'CASH', 'value': 100},
            {'date': '2025-01-01', 'stock_id': '2330', 'shares': 0, 'value': 0},
            {'date': '2025-01-01', 'stock_id': '2330', 'shares': 0, 'value': 0}]
    assert reconcile(100, [['2025-01-01', 100]], [], [], rows, [])['status'] == 'NOT_PUBLISHABLE'


def test_fill_cash_must_match_price_and_costs():
    from core.research.platform.ledger_v2 import reconcile
    fill = {'fill_id': 'f', 'date': '2025-01-01', 'side': 'BUY', 'stock_id': '2330', 'shares': 1,
            'price': 10, 'notional': 10, 'fee': 0, 'tax': 0, 'cash_delta': -1}
    rows = [{'date': '2025-01-01', 'stock_id': 'CASH', 'value': 99},
            {'date': '2025-01-01', 'stock_id': '2330', 'shares': 1, 'value': 1}]
    assert reconcile(100, [['2025-01-01', 100]], [fill], [], rows,
                     [{'stock_id': '2330', 'shares': 1}])['status'] == 'NOT_PUBLISHABLE'


def test_negative_initial_and_missing_fill_fields_fail_closed():
    from core.research.platform.ledger_v2 import reconcile
    day = '2025-01-01'
    assert reconcile(-10, [[day, -10]], [], [], [{'date': day, 'stock_id': 'CASH', 'value': -10}], [])['status'] == 'NOT_PUBLISHABLE'
    assert reconcile(100, [[day, 100]], [{'date': day}], [], [{'date': day, 'stock_id': 'CASH', 'value': 100}], [])['status'] == 'NOT_PUBLISHABLE'


def test_duplicate_corporate_action_and_wrong_cash_after_fail_closed():
    from core.research.platform.ledger_v2 import reconcile
    day = '2025-01-01'
    action = {'date': day, 'stock_id': '2330', 'status': 'BOOKED', 'event_id': 'a', 'shares_before': 0, 'shares_after': 0, 'cash_credited': 1}
    rows = [{'date': day, 'stock_id': 'CASH', 'value': 102}]
    assert reconcile(100, [[day, 102]], [], [action, action], rows, [])['status'] == 'NOT_PUBLISHABLE'
    fill = {'date': day, 'fill_id': 'f', 'stock_id': '2330', 'side': 'BUY', 'shares': 1, 'price': 10,
            'notional': 10, 'fee': 0, 'tax': 0, 'cash_delta': -10, 'cash_after': 999}
    rows = [{'date': day, 'stock_id': 'CASH', 'value': 90}, {'date': day, 'stock_id': '2330', 'shares': 1, 'value': 10}]
    assert reconcile(100, [[day, 100]], [fill], [], rows, [{'stock_id': '2330', 'shares': 1}])['status'] == 'NOT_PUBLISHABLE'


def test_duplicate_final_positions_and_empty_stock_fail_closed():
    from core.research.platform.ledger_v2 import reconcile
    day = '2025-01-01'
    fill = {'date': day, 'fill_id': 'f', 'stock_id': '2330', 'side': 'BUY', 'shares': 1, 'price': 10,
            'notional': 10, 'fee': 0, 'tax': 0, 'cash_delta': -10, 'cash_after': 90}
    rows = [{'date': day, 'stock_id': 'CASH', 'value': 90}, {'date': day, 'stock_id': '2330', 'shares': 1, 'value': 10}]
    end = [{'stock_id': '2330', 'shares': 2}, {'stock_id': '2330', 'shares': 1}]
    assert reconcile(100, [[day, 100]], [fill], [], rows, end)['status'] == 'NOT_PUBLISHABLE'
    fill['stock_id'] = rows[1]['stock_id'] = ''
    assert reconcile(100, [[day, 100]], [fill], [], rows, [{'stock_id': '', 'shares': 1}])['status'] == 'NOT_PUBLISHABLE'


def test_fractional_fill_and_orphan_duplicate_action_fail_closed():
    from core.research.platform.ledger_v2 import reconcile
    day = '2025-01-01'
    fill = {'date': day, 'fill_id': 'f', 'stock_id': '2330', 'side': 'BUY', 'shares': 0.5, 'price': 10,
            'notional': 5, 'fee': 0, 'tax': 0, 'cash_delta': -5, 'cash_after': 95}
    rows = [{'date': day, 'stock_id': 'CASH', 'value': 95}, {'date': day, 'stock_id': '2330', 'shares': 0.5, 'value': 5}]
    assert reconcile(100, [[day, 100]], [fill], [], rows, [{'stock_id': '2330', 'shares': 0.5}])['status'] == 'NOT_PUBLISHABLE'
    action = {'date': day, 'stock_id': '2330', 'event_id': 'a', 'status': 'DUPLICATE_SUPPRESSED'}
    assert reconcile(100, [[day, 100]], [], [action], [{'date': day, 'stock_id': 'CASH', 'value': 100}], [])['status'] == 'NOT_PUBLISHABLE'
