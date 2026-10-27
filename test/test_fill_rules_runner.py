"""BacktestEngine next_session_open_v2: the shared fill rule rejects locked, untraded and bar-less opens; legacy fixtures are untouched."""
import pytest

from test_next_session_execution import engine


class Research:
    def __init__(self, bars):
        self.bars = bars

    def bar(self, sid, day):
        return self.bars.get(day)


def full(open_, high=None, low=None, volume=1000, amount=1e9):
    return {'open_price': open_, 'high_price': high if high is not None else open_ + 1, 'low_price': low if low is not None else open_ - 1,
            'close_price': open_, 'volume': volume, 'amount': amount}


def research_engine(bar_on_19):
    e = engine()
    e.trading_sessions = ['2026-10-16', '2026-10-19', '2026-10-20']
    e.research_data = Research({'2026-10-16': full(100), '2026-10-19': bar_on_19})
    e.get_data = lambda sid, day: e.research_data.bar(sid, day)
    return e


@pytest.mark.parametrize('bar,reason', [
    (full(110.0, 110.0, 110.0), 'REJECTED_LOCKED_LIMIT_UP'),
    (full(100.0, volume=0), 'REJECTED_NO_TRADE'),
    (full(100.0, amount=float('nan')), 'REJECTED_NO_TRADE'),
])
def test_rejected_buy_never_opens_a_position(bar, reason):
    e = research_engine(bar)
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    assert not e.positions and e.capital == e.initial_capital
    assert e.execution_events[0]['status'] == 'UNFILLED' and e.execution_events[0]['reason'] == reason


def test_tradable_open_fills_and_locked_down_sell_keeps_position():
    e = research_engine(full(100.0))
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    assert e.positions['2330']['cost'] == 100
    e.research_data.bars['2026-10-20'] = full(90.0, 90.0, 90.0)
    e.pending_orders.append({'side': 'SELL', 'stock_id': '2330', 'signal_date': '2026-10-19', 'reason': 'EXIT'})
    e.execute_pending_open('2026-10-20')
    assert '2330' in e.positions and not e.trades
    assert e.execution_events[-1]['reason'] == 'REJECTED_LOCKED_LIMIT_DOWN'


def test_missing_previous_session_bar_is_no_bar_not_a_stale_reference():
    e = research_engine(full(100.0))
    del e.research_data.bars['2026-10-16']  # no bar on the previous session: no limit band
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    assert not e.positions and e.execution_events[0]['reason'] == 'REJECTED_NO_BAR'


def test_cap_uses_the_previous_session_amount_never_the_same_day():
    """No look-ahead: 5% of the previous session's traded value sizes the open order; the open session's own value is only reported."""
    filled = []
    for same_day in (1.0, 1e12):
        e = research_engine(full(100.0, amount=same_day))
        e.research_data.bars['2026-10-16'] = full(100.0, amount=200_000)
        e.process_close_signals('2026-10-16', 'BULL')
        e.execute_pending_open('2026-10-19')
        event = e.execution_events[0]
        assert (event['status'], event['reason']) == ('PARTIAL', 'PARTICIPATION_CAP') and 'fill_rule' not in event
        assert event['same_day_amount_share'] == pytest.approx(100 * 100.0 / same_day)
        filled.append(e.positions['2330']['shares'])
    assert filled == [100, 100] and e.fill_rule == 'CORRECTED_FILL_RULE'


def test_capped_sell_is_partial_and_the_rest_keeps_its_pro_rata_cost():
    e = research_engine(full(100.0))
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    held, cost = e.positions['2330']['shares'], e.positions['2330']['total_cost']
    e.research_data.bars['2026-10-19'] = full(100.0, amount=100_000)  # the previous session of 2026-10-20
    e.research_data.bars['2026-10-20'] = full(100.0)
    e.pending_orders.append({'side': 'SELL', 'stock_id': '2330', 'signal_date': '2026-10-19', 'reason': 'EXIT'})
    e.execute_pending_open('2026-10-20')
    assert e.positions['2330']['shares'] == held - 50 and e.execution_events[-1]['status'] == 'PARTIAL'
    assert e.positions['2330']['total_cost'] == pytest.approx(cost * (held - 50) / held, rel=1e-9, abs=1e-12)


def test_unknown_previous_amount_fills_nothing():
    e = research_engine(full(100.0))
    e.research_data.bars['2026-10-16'] = full(100.0, amount=None)
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    assert not e.positions and e.capital == e.initial_capital and e.execution_events[0]['reason'] == 'REJECTED_PARTICIPATION_CAP'


def test_bar_without_trade_fields_runs_but_is_diagnostic_only():
    e = engine()  # legacy fixture: open/close only, no research bars
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    assert e.positions and e.unchecked_fill_orders == 1 and e.fill_rule == 'DIAGNOSTIC_ONLY'


def test_partial_exits_book_one_trade_with_value_weighted_profit():
    e = research_engine(full(100.0))
    e.process_close_signals('2026-10-16', 'BULL')
    e.execute_pending_open('2026-10-19')
    cost, cash_before = e.positions['2330']['total_cost'], e.capital
    held = e.positions['2330']['shares']
    e.research_data.bars['2026-10-19'] = full(100.0, amount=100_000)  # caps the 2026-10-20 sell to 50 shares
    e.research_data.bars['2026-10-20'] = full(110.0)
    e.trading_sessions.append('2026-10-21')
    e.research_data.bars['2026-10-21'] = full(110.0)  # previous session 2026-10-20 is liquid: the rest sells
    for day, prev in (('2026-10-20', '2026-10-19'), ('2026-10-21', '2026-10-20')):
        e.pending_orders.append({'side': 'SELL', 'stock_id': '2330', 'signal_date': prev, 'reason': 'EXIT'})
        e.execute_pending_open(day)
    assert [ev['status'] for ev in e.execution_events[1:]] == ['PARTIAL', 'FILLED'] and not e.positions
    assert len(e.trades) == 1 and e.trade_count == 1 and e.win_count == 1
    net = e.capital - cash_before
    assert e.trades[0]['profit_pct'] == pytest.approx((net - cost) / cost * 100, rel=1e-9, abs=1e-12) and held > 50


def test_a_run_without_orders_is_never_corrected():
    e = engine()
    assert e.fill_rule == 'DIAGNOSTIC_ONLY'
