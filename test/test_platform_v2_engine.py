"""Synthetic engine scenarios for the V2 fill ledger (design D3). Not investment evidence."""
import pytest

from core.backtest import runner
from core.backtest.costs import CostModel
from core.backtest.portfolio import Portfolio
from core.research.platform.engine_v2 import BacktestEngineV2, rule_engine

DAYS = {'2026-01-05': {'open_price': 100.0, 'close_price': 100.0},
        '2026-01-06': {'open_price': 100.0, 'close_price': 101.0},
        '2026-01-07': {'open_price': 110.0, 'close_price': 110.0}}


@pytest.fixture(autouse=True)
def no_slippage(monkeypatch):
    monkeypatch.setattr(runner.Config, 'SLIPPAGE_MAX_PIPS', 0.0, raising=False)


def engine(rule='LOT_ENGINE_V1', capital=1_000_000, data=DAYS):
    cls = rule_engine(rule)
    e = cls.__new__(cls)
    e.execution_contract, e.research_data = 'next_session_open_v2', None
    e.pending_orders, e.execution_events, e._execution_asof = [], [], None
    e.initial_capital = capital
    e.portfolio = Portfolio(capital)
    e.costs = CostModel(0.001425, 0.003, 20)
    e._get_max_holdings = lambda day: 3
    e.stop_loss_pct, e.take_profit_pct, e.max_hold_days = .05, .2, 10
    e.mode, e.strategy_obj = 'v30', None
    e.trade_count, e.win_count, e.trades = 0, 0, []
    e.get_data = lambda sid, day: data.get(day)
    return e


def order(e, side, day='2026-01-05', sid='2330'):
    e.pending_orders.append({'side': side, 'stock_id': sid, 'signal_date': day, 'reason': 'TEST'})


def test_buy_and_full_sell_produce_one_fill_each_and_cash_matches():
    e = engine()
    order(e, 'BUY')
    e.execute_pending_open('2026-01-06')
    order(e, 'SELL', '2026-01-06')
    e.execute_pending_open('2026-01-07')
    buy, sell = e.fill_rows()
    assert (buy['side'], buy['shares'], buy['price'], buy['status']) == ('BUY', 2000, 100.0, 'FILLED')
    assert (sell['side'], sell['shares'], sell['price'], sell['status']) == ('SELL', 2000, 110.0, 'FILLED')
    assert buy['cash_delta'] == -(200_000 + buy['fee']) and buy['tax'] == 0
    assert sell['cash_delta'] == 220_000 - sell['fee'] - sell['tax'] and sell['tax'] == 660
    assert e.capital == pytest.approx(1_000_000 + buy['cash_delta'] + sell['cash_delta'], abs=1e-9)
    assert [x.get('fill_id') for x in e.execution_events] == [buy['fill_id'], sell['fill_id']]
    assert sell['cash_after'] == e.capital


def test_partial_sell_is_a_fill_not_a_trade():
    e = engine()
    order(e, 'BUY')
    e.execute_pending_open('2026-01-06')
    e._fill = {'checked': True, 'prior_amount': 2_200_100.0}  # 5% cap allows 1000 shares at 110
    e.sell('2330', 110.0, '2026-01-07', 'TEST')
    e._fill = None
    fill = e.fill_rows()[-1]
    assert (fill['side'], fill['shares'], fill['status']) == ('SELL', 1000, 'PARTIAL')
    assert not e.trades and e.positions['2330']['shares'] == 1000


def test_partial_order_fills_remainder_next_day_and_replay_is_idempotent():
    e = engine()
    e.buy('2330', 100.0, '2026-01-06', order_id='buy')
    e._fill = {'checked': True, 'prior_amount': 2_200_100.0}
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell')
    cash, shares = e.capital, e.positions['2330']['shares']
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell')
    assert e.capital == cash and e.positions['2330']['shares'] == shares
    e._fill = None
    e.sell('2330', 110.0, '2026-01-08', 'TEST', order_id='sell')
    fills = [row for row in e.fill_rows() if row['order_id'] == 'sell']
    assert [row['shares'] for row in fills] == [1000, 1000]
    assert len({row['fill_id'] for row in fills}) == 2
    assert '2330' not in e.positions
    cash = e.capital
    e.sell('2330', 110.0, '2026-01-08', 'TEST', order_id='sell')
    assert e.capital == cash and len(e.fill_rows()) == 3


def test_same_order_distinct_intraday_fill_events_and_replay():
    e = engine()
    e.buy('2330', 100.0, '2026-01-06', order_id='buy')
    e._fill = {'checked': True, 'prior_amount': 2_200_100.0}
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell', event_id='first')
    cash = e.capital
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell', event_id='first')
    assert e.capital == cash and e.positions['2330']['shares'] == 1000
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell', event_id='second')
    assert '2330' not in e.positions
    assert len({row['fill_id'] for row in e.fill_rows() if row['order_id'] == 'sell'}) == 2


def test_same_fill_event_replayed_on_another_day_fails_closed():
    e = engine()
    e.buy('2330', 100.0, '2026-01-06', order_id='buy')
    e._fill = {'checked': True, 'prior_amount': 2_200_100.0}
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell', event_id='first')
    cash = e.capital
    with pytest.raises(ValueError, match='FILL_EVENT_ID_CONFLICT'):
        e.sell('2330', 110.0, '2026-01-08', 'TEST', order_id='sell', event_id='first')
    assert e.capital == cash and e.positions['2330']['shares'] == 1000


@pytest.mark.parametrize(('day', 'price'), [('2026-01-08', 110.0), ('2026-01-07', 111.0)])
def test_reusing_fill_event_id_with_changed_payload_fails_closed(day, price):
    e = engine()
    e.buy('2330', 100.0, '2026-01-06', order_id='buy')
    e._fill = {'checked': True, 'prior_amount': 2_200_100.0}
    e.sell('2330', 110.0, '2026-01-07', 'TEST', order_id='sell', event_id='first')
    cash, shares = e.capital, e.positions['2330']['shares']

    with pytest.raises(ValueError, match='FILL_EVENT_ID_CONFLICT'):
        e.sell('2330', price, day, 'TEST', order_id='sell', event_id='first')

    assert e.capital == cash and e.positions['2330']['shares'] == shares


def test_unfilled_order_has_no_fill():
    e = engine(data={'2026-01-06': {'open_price': None}})
    order(e, 'BUY')
    e.execute_pending_open('2026-01-06')
    assert e.fill_rows() == [] and e.execution_events[0]['status'] == 'UNFILLED' and 'fill_id' not in e.execution_events[0]


def test_failed_fill_rolls_back_and_retry_records_once(monkeypatch):
    e = engine()
    original = Portfolio.open_position
    calls = []

    def flaky(self, sid, info):
        calls.append(sid)
        if len(calls) == 1:
            raise OSError('simulated crash after debit')
        return original(self, sid, info)
    monkeypatch.setattr(Portfolio, 'open_position', flaky)
    with pytest.raises(OSError):
        e.buy('2330', 100.0, '2026-01-06')
    assert e.capital == 1_000_000 and not e.positions and e.fill_rows() == []
    e.buy('2330', 100.0, '2026-01-06')
    e.buy('2330', 100.0, '2026-01-06')  # duplicate: already held, no second fill
    assert len(e.fill_rows()) == 1


def test_duplicate_fill_event_is_ignored():
    e = engine()
    e.buy('2330', 100.0, '2026-01-06')
    row = e.fill_rows()[0]
    e._record_fill(dict(row))
    assert e.fill_rows() == [row]


def test_distinct_same_day_orders_fill_and_explicit_replay_is_idempotent():
    e = engine()
    e.buy('2330', 100, '2026-01-06', order_id='buy-1')
    e.sell('2330', 110, '2026-01-06', 'TEST', order_id='sell-1')
    e.buy('2330', 100, '2026-01-06', order_id='buy-2')
    assert e.positions['2330']['shares'] == 2000
    assert len(e.fill_rows()) == 3
    cash = e.capital
    e.sell('2330', 110, '2026-01-06', 'TEST', order_id='sell-1')
    assert e.capital == cash and e.positions['2330']['shares'] == 2000
    e.sell('2330', 110, '2026-01-07', 'TEST', order_id='sell-1')
    assert e.capital == cash and e.positions['2330']['shares'] == 2000


def test_distinct_pending_orders_and_replay_use_stable_order_ids():
    e = engine()
    order(e, 'BUY')
    e.execute_pending_open('2026-01-06')
    original = e.execution_events[0]
    order(e, 'SELL')
    e.execute_pending_open('2026-01-06')
    order(e, 'BUY')
    e.execute_pending_open('2026-01-06')
    assert len(e.fill_rows()) == 3 and e.positions['2330']['shares'] == 2000
    e.pending_orders.append({k: original[k] for k in ('side', 'stock_id', 'signal_date', 'order_id')} | {'reason': 'TEST'})
    cash = e.capital
    e.execute_pending_open('2026-01-06')
    assert e.capital == cash and len(e.fill_rows()) == 3


def test_board_lot_rule_buys_multiples_of_1000():
    e = engine('LOT_BOARD_1000')
    e.buy('2330', 70.0, '2026-01-06')  # budget 200,000 / 70 = 2857 shares
    assert e.fill_rows()[0]['shares'] == 2000


def test_odd_lot_rule_buys_below_one_board_lot():
    v1, odd = engine(capital=100_000), engine('LOT_ODD', capital=100_000)
    v1.buy('2330', 500.0, '2026-01-06')  # budget 20,000 < one board lot of 500,000
    odd.buy('2330', 500.0, '2026-01-06')
    assert v1.fill_rows() == [] and odd.fill_rows()[0]['shares'] == 40


def test_rule_comparability_flag():
    assert rule_engine('LOT_ENGINE_V1').comparable_with_v1 is True
    assert rule_engine('LOT_BOARD_1000').comparable_with_v1 is False
    assert rule_engine('LOT_ODD').comparable_with_v1 is False
    with pytest.raises(ValueError):
        rule_engine('LOT_MAGIC')


def test_v2_is_a_backtest_engine():
    assert issubclass(BacktestEngineV2, runner.BacktestEngine)


def test_pending_failure_restores_all_orders_and_earlier_fills(monkeypatch):
    e = engine()
    order(e, 'BUY', sid='2330')
    order(e, 'BUY', sid='2317')
    original = e.get_data
    def fail_second(sid, day):
        if sid == '2317':
            raise RuntimeError('second order failed')
        return original(sid, day)
    e.get_data = fail_second
    with pytest.raises(RuntimeError, match='second order'):
        e.execute_pending_open('2026-01-06')
    assert e.capital == 1_000_000 and not e.positions and not e.fills
    assert len(e.pending_orders) == 2 and not e.execution_events
    e.get_data = original
    e.execute_pending_open('2026-01-06')
    assert len(e.fill_rows()) == 2 and len(e.execution_events) == 2


def test_corporate_action_failure_restores_event_and_account(monkeypatch):
    e = engine()
    e.buy('2330', 100, '2026-01-06')
    e.corporate_actions = {'2026-01-07': [{'stock_id': '2330', 'event_id': 'ca1', 'kind': 'DIVIDEND',
        'terms_status': 'REFERENCE_DERIVED', 'price_factor': 1, 'share_multiplier': 1, 'cash_per_share': 1}]}
    e._applied_action_ids, e.corporate_action_events = set(), []
    cash, shares = e.capital, e.positions['2330']['shares']
    original = Portfolio.credit
    def fail_credit(self, amount):
        original(self, amount)
        raise RuntimeError('credit failed')
    monkeypatch.setattr(Portfolio, 'credit', fail_credit)
    with pytest.raises(RuntimeError, match='credit failed'):
        e._apply_corporate_actions('2026-01-07')
    assert e.capital == cash and e.positions['2330']['shares'] == shares
    assert not e._applied_action_ids and not e.corporate_action_events
    monkeypatch.setattr(Portfolio, 'credit', original)
    e._apply_corporate_actions('2026-01-07')
    assert e.capital == cash + shares and len(e.corporate_action_events) == 1
