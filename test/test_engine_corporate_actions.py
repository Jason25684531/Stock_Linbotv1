"""POSITION_LEDGER_CA_V1 accounting (change 2026-11-10-engine-accounting-and-history-gaps-v1): synthetic engineering checks, not performance evidence."""
import math

import pytest

from core.backtest.costs import CostModel
from core.backtest.portfolio import Portfolio
from core.backtest.runner import ACCOUNTING_LEDGER, ACCOUNTING_LEGACY, PAR_VALUE, BacktestEngine


def event(stock='2330', date='2026-10-20', kind='CASH_DIVIDEND', cash=0.0, multiplier=1.0, factor=1.0, status='REFERENCE_DERIVED', event_id=None):
    return {'event_id': event_id or f'{stock}:{date}:{kind}:{cash}:{multiplier}', 'stock_id': stock, 'date': date, 'kind': kind, 'cash_per_share': cash,
            'share_multiplier': multiplier, 'price_factor': factor, 'terms_status': status, 'source_file': 'TWT49U_test.json', 'source_version': 'v1'}


def engine(actions=None, prices=None):
    """A research-contract engine with the ledger wired in; marks use the finite_positive_stale policy on the stubbed closes."""
    e = BacktestEngine.__new__(BacktestEngine)
    e.execution_contract, e.research_data = 'next_session_open_v2', object()
    e.valuation_policy, e.valuation_events = 'finite_positive_stale', []
    e.pending_orders, e.execution_events = [], []
    e._execution_asof = None
    e.initial_capital = 1_000_000
    e.portfolio = Portfolio(e.initial_capital)
    e.costs = CostModel(0.0, 0.0, 0)
    e._apply_execution_slippage = lambda price, side: price
    e._get_max_holdings = lambda day: 3
    e.stop_loss_pct, e.take_profit_pct, e.max_hold_days = .05, .2, 10
    e.mode, e.strategy_obj = 'v30', None
    e.trade_count, e.win_count, e.trades = 0, 0, []
    e.corporate_actions = actions
    e.accounting = ACCOUNTING_LEDGER if actions is not None else ACCOUNTING_LEGACY
    e.corporate_action_events, e._applied_action_ids = [], set()
    e.prices = prices or {'2026-10-19': 100.0, '2026-10-20': 97.0, '2026-10-21': 98.0}
    e.get_data = lambda sid, day: ({'open_price': e.prices[day], 'close_price': e.prices[day]} if day in e.prices else None)
    return e


def hold(e, shares=1000, price=100.0, day='2026-10-19'):
    e.portfolio = Portfolio(0.0)  # fully invested: cash afterwards is exactly what the ledger credited
    e.portfolio.open_position('2330', {'shares': shares, 'cost': price, 'total_cost': shares * price, 'days': 0, 'stop_loss': price * .95,
                                       'highest': price, 'buy_date': day, 'last_valid_close': price, 'last_valid_date': day, 'stale_days': 0})
    return e


def nav(e, day):
    return e.capital + sum(e._mark_position(sid, info, day) for sid, info in e.positions.items())


def test_cash_dividend_is_credited_not_a_loss():
    e = hold(engine({'2026-10-20': [event(cash=3.0, factor=0.97)]}))
    before = nav(e, '2026-10-19')
    e._apply_corporate_actions('2026-10-20')
    info = e.positions['2330']
    assert info['shares'] == 1000 and e.capital == pytest.approx(3000.0) and info['distributions'] == pytest.approx(3000.0)
    assert info['cost'] == pytest.approx(97.0) and info['stop_loss'] == pytest.approx(95 * .97) and info['highest'] == pytest.approx(97.0)
    assert nav(e, '2026-10-20') == pytest.approx(before)  # 97 * 1000 + 3000 = 100 * 1000
    assert e.corporate_action_events[0]['status'] == 'BOOKED' and e.corporate_action_events[0]['cash_credited'] == pytest.approx(3000.0)


def test_stock_dividend_adjusts_shares_and_pays_the_fraction_at_par():
    e = hold(engine({'2026-10-20': [event(kind='STOCK_DIVIDEND', multiplier=1.1, factor=1 / 1.1)]}, {'2026-10-19': 100.0, '2026-10-20': 100 / 1.1}), shares=1005)
    before = nav(e, '2026-10-19')
    e._apply_corporate_actions('2026-10-20')
    assert e.positions['2330']['shares'] == 1105  # floor(1005 * 1.1 = 1105.5)
    assert e.capital == pytest.approx(0.5 * PAR_VALUE)
    assert nav(e, '2026-10-20') == pytest.approx(before - 0.5 * (100 / 1.1 - PAR_VALUE))  # the half share is settled at par, as TWSE does


def test_large_stock_dividend_like_1808_keeps_nav_and_the_trade_is_not_a_54_percent_loss():
    # TWT49U 2024-09-26 1808: pre 119.50, reference 52.95 (權息); the terms fall back to ASSUMED_STOCK: multiplier 2.25685, factor 0.443096
    prices = {'2026-10-19': 119.5, '2026-10-20': 53.5, '2026-10-21': 54.0}
    e = hold(engine({'2026-10-20': [event(kind='CASH_AND_STOCK_DIVIDEND', multiplier=2.25685, factor=52.95 / 119.5, status='ASSUMED_STOCK')]}, prices),
             shares=1000, price=119.5)
    e._apply_corporate_actions('2026-10-20')
    assert e.positions['2330']['shares'] == 2256 and e.capital == pytest.approx(0.85 * PAR_VALUE)
    assert nav(e, '2026-10-20') == pytest.approx(2256 * 53.5 + 8.5)
    e.sell('2330', 54.0, '2026-10-21', 'test')
    trade = e.trades[0]
    assert trade['buy_price'] == 119.5 and trade['adjusted_buy_price'] == pytest.approx(119.5 * 52.95 / 119.5) and trade['exit_shares'] == 2256
    assert trade['profit_pct'] == pytest.approx((2256 * 54.0 + 8.5 - 119500) / 119500 * 100)  # about +1.9 %, never -54 %


def test_capital_reduction_returns_cash_and_cancels_shares():
    e = hold(engine({'2026-10-20': [event(kind='CAPITAL_REDUCTION_CASH', cash=3.0, multiplier=0.7, factor=41.28 / 31.9)]}, {'2026-10-19': 31.9, '2026-10-20': 41.28}),
             shares=1000, price=31.9)
    before = nav(e, '2026-10-19')
    e._apply_corporate_actions('2026-10-20')
    assert e.positions['2330']['shares'] == 700 and e.capital == pytest.approx(3000.0)
    assert nav(e, '2026-10-20') == pytest.approx(before, rel=1e-3)


def test_par_value_change_multiplies_shares_without_profit():
    e = hold(engine({'2026-10-20': [event(kind='PAR_VALUE_CHANGE', multiplier=10.0, factor=0.1)]}, {'2026-10-19': 190.0, '2026-10-20': 19.0}), shares=1000, price=190.0)
    e._apply_corporate_actions('2026-10-20')
    assert e.positions['2330']['shares'] == 10000 and e.capital == 0
    assert nav(e, '2026-10-20') == pytest.approx(190_000.0) and e.positions['2330']['cost'] == pytest.approx(19.0)


def test_two_events_on_one_day_are_booked_in_ledger_order():
    rows = [event(kind='CASH_DIVIDEND', cash=1.0, factor=0.99, event_id='a'), event(kind='CAPITAL_REDUCTION_LOSS', multiplier=0.5, factor=2.0, event_id='b')]
    e = hold(engine({'2026-10-20': rows}, {'2026-10-19': 100.0, '2026-10-20': 198.0}))
    e._apply_corporate_actions('2026-10-20')
    assert [x['event_id'] for x in e.corporate_action_events] == ['a', 'b']
    assert e.positions['2330']['shares'] == 500 and e.capital == pytest.approx(1000.0)
    assert e.positions['2330']['cost'] == pytest.approx(100 * 0.99 * 2.0)


def test_same_event_id_is_never_booked_twice():
    e = hold(engine({'2026-10-20': [event(cash=3.0, factor=0.97, event_id='x'), event(cash=3.0, factor=0.97, event_id='x')]}))
    e._apply_corporate_actions('2026-10-20')
    e._apply_corporate_actions('2026-10-20')
    assert e.capital == pytest.approx(3000.0) and e.positions['2330']['cost'] == pytest.approx(97.0)
    assert [x['status'] for x in e.corporate_action_events] == ['BOOKED', 'DUPLICATE_SUPPRESSED', 'DUPLICATE_SUPPRESSED', 'DUPLICATE_SUPPRESSED']


def test_unresolved_terms_are_recorded_and_never_booked():
    e = hold(engine({'2026-10-20': [event(status='UNRESOLVED', cash=5.0, multiplier=0.5)]}))
    e._apply_corporate_actions('2026-10-20')
    assert e.capital == 0 and e.positions['2330']['shares'] == 1000 and e.corporate_action_events[0]['status'] == 'UNRESOLVED_NOT_BOOKED'


def test_entitlement_belongs_to_the_holder_at_the_previous_close():
    e = engine({'2026-10-20': [event(cash=3.0, factor=0.97)]})
    e.find_candidates = lambda day: ['2330']
    e.process_close_signals('2026-10-19', 'BULL')  # BUY signal, fills at the 10-20 open: bought ex-dividend
    e._apply_corporate_actions('2026-10-20')
    e.execute_pending_open('2026-10-20')
    assert '2330' in e.positions and e.positions['2330'].get('distributions') is None and not e.corporate_action_events


def test_a_position_sold_at_the_ex_date_open_still_receives_the_distribution():
    e = hold(engine({'2026-10-20': [event(cash=3.0, factor=0.97)]}))
    e.pending_orders.append({'side': 'SELL', 'stock_id': '2330', 'signal_date': '2026-10-19', 'reason': 'exit'})
    e._apply_corporate_actions('2026-10-20')
    e.execute_pending_open('2026-10-20')
    assert e.trades[0]['distributions'] == pytest.approx(3000.0) and e.trades[0]['profit_pct'] == pytest.approx(0.0)  # 97 * 1000 + 3000 = 100 * 1000


def test_open_position_at_window_end_is_valued_with_adjusted_shares_and_received_cash():
    e = hold(engine({'2026-10-20': [event(kind='STOCK_DIVIDEND', multiplier=1.2, factor=1 / 1.2)]}, {'2026-10-19': 120.0, '2026-10-20': 100.0, '2026-10-21': 110.0}),
             price=120.0)
    e._apply_corporate_actions('2026-10-20')
    assert e.positions['2330']['shares'] == 1200
    assert nav(e, '2026-10-21') == pytest.approx(1200 * 110.0)  # the end mark is the adjusted holding at the last reliable close


def test_ordinary_trade_without_events_matches_the_legacy_trade_record():
    e = hold(engine({}))
    e._apply_corporate_actions('2026-10-20')
    e.sell('2330', 98.0, '2026-10-21', 'test')
    assert set(e.trades[0]) == {'stock_id', 'strategy', 'buy_date', 'sell_date', 'buy_price', 'sell_price', 'profit_pct', 'reason', 'days'}
    assert e.trades[0]['profit_pct'] == pytest.approx(-2.0) and not e.corporate_action_events


def test_engine_without_a_ledger_keeps_legacy_accounting():
    e = hold(engine(None))
    e._apply_corporate_actions('2026-10-20')
    assert e.accounting == ACCOUNTING_LEGACY and e.capital == 0 and not e.corporate_action_events


def test_full_cash_out_closes_the_position_as_a_trade():
    e = hold(engine({'2026-10-20': [event(kind='CAPITAL_REDUCTION_CASH', cash=10.0, multiplier=0.0, factor=1.0)]}))
    e._apply_corporate_actions('2026-10-20')
    assert not e.positions and e.trades[0]['reason'].startswith('CORPORATE_ACTION_CASH_OUT') and e.trades[0]['profit_pct'] == pytest.approx(-90.0)


def test_saturday_make_up_session_is_a_valid_research_session():
    with pytest.raises(ValueError, match='requires research_data'):  # it is past the calendar check: the weekday is no longer refused
        BacktestEngine(execution_contract='next_session_open_v2', trading_sessions=['2016-01-29', '2016-01-30'], persist_results=False, persist_to_db=False)


def test_ledger_needs_the_research_contract():
    with pytest.raises(ValueError, match='corporate_actions is a research accounting option'):
        BacktestEngine(execution_contract='legacy_same_close', corporate_actions={}, persist_results=False, persist_to_db=False)


def test_end_mark_after_an_event_without_a_later_close_uses_the_adjusted_carried_mark():
    """Codex round 1: last traded close 100, 2-for-1 stock dividend at reference 50, no valid close afterwards -> 100 per original share, not 200."""
    e = hold(engine({'2026-10-20': [event(kind='STOCK_DIVIDEND', multiplier=2.0, factor=0.5)]}, {'2026-10-19': 100.0}))
    bars = {('2330', '2026-10-19'): {'close_price': 100.0}, ('2330', '2026-10-21'): {'close_price': float('nan'), 'volume': 0}}
    e.research_data = type('R', (), {'bar': lambda self, sid, day: bars.get((sid, day)), 'last_close': lambda self, sid, day: 100.0})()
    e._apply_corporate_actions('2026-10-20')
    info = e.positions['2330']
    assert info['shares'] == 2000 and e._ledger_end_mark('2330', info, '2026-10-21') == pytest.approx(50.0)
    assert info['shares'] * e._ledger_end_mark('2330', info, '2026-10-21') == pytest.approx(100_000.0)
    bars[('2330', '2026-10-21')] = {'close_price': 52.0}
    assert e._ledger_end_mark('2330', info, '2026-10-21') == 52.0


def test_cash_out_trade_carries_entry_shares_through_the_evidence_path():
    """Codex round 2: a zero-multiplier cash return closes the position without sell(); the evidence wrapper must still see shares and costs."""
    from scripts.legacy_baseline_run import _CostLog, _evidence, _record_trades
    e = engine({'2026-10-20': [event(kind='CAPITAL_REDUCTION_CASH', cash=10.0, multiplier=0.0, factor=1.0)]})
    e.costs = _CostLog(CostModel(0.001425, 0.003, 20))
    _record_trades(e, e.costs)
    e.buy('2330', 100.0, '2026-10-19')
    shares = e.positions['2330']['shares']
    e._apply_corporate_actions('2026-10-20')
    trade = e.trades[0]
    assert trade['shares'] == shares and trade['exit_shares'] == 0 and trade['cost'] > 0 and trade['distributions'] == pytest.approx(10.0 * shares)
    e.daily_assets = [1_000_000.0]
    open_rows, summary = _evidence(e, [], e.costs, ['2026-10-19', '2026-10-20'])
    assert open_rows == [] and summary['traded_value'] == pytest.approx(100.0 * shares)
