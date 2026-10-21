"""Synthetic engineering checks only; these are not investment performance evidence."""
import pytest

from core.backtest.runner import BacktestEngine, PortfolioBacktestEngine
from core.backtest.portfolio import Portfolio
from core.backtest.costs import CostModel


def engine(contract='next_session_open_v2'):
    e = BacktestEngine.__new__(BacktestEngine)
    e.execution_contract = contract
    e.pending_orders, e.execution_events = [], []
    e._execution_asof = None
    e.initial_capital = 1_000_000
    e.portfolio = Portfolio(e.initial_capital)
    e.costs = CostModel(0.001425, 0.003, 20)
    e._apply_execution_slippage = lambda price, side: price
    e._get_max_holdings = lambda day: 3
    e.stop_loss_pct, e.take_profit_pct, e.max_hold_days = .05, .2, 10
    e.mode, e.strategy_obj = 'v30', None
    e.trade_count, e.win_count, e.trades = 0, 0, []
    e.find_candidates = lambda day: ['2330']
    data = {'2026-10-16': {'open_price': 999, 'close_price': 999},
            '2026-10-19': {'open_price': 100, 'close_price': 80},
            '2026-10-20': {'open_price': 70, 'close_price': 90}}
    e.get_data = lambda sid, day: data.get(day)
    return e


def test_close_signals_fill_at_next_observed_open_for_both_entry_and_exit():
    e = engine()
    observed_limits = []
    e._get_max_holdings = lambda day: observed_limits.append(day) or 3
    e.process_close_signals('2026-10-16', 'BULL')
    assert not e.positions
    e.execute_pending_open('2026-10-19')
    assert e.positions['2330']['cost'] == 100
    assert observed_limits == ['2026-10-16']  # no execution-day news used at open
    e.process_close_signals('2026-10-19', 'BULL')
    assert not e.trades and '2330' in e.positions
    e.execute_pending_open('2026-10-20')
    assert e.trades[0]['buy_date'] == '2026-10-19'
    assert e.trades[0]['sell_date'] == '2026-10-20'
    assert e.trades[0]['sell_price'] == 70
    assert [(x['side'], x['signal_date'], x['execution_date'], x['status']) for x in e.execution_events] == [
        ('BUY', '2026-10-16', '2026-10-19', 'FILLED'), ('SELL', '2026-10-19', '2026-10-20', 'FILLED')]


@pytest.mark.parametrize('price', [None, 0, -1, float('nan'), float('inf')])
def test_missing_open_expires_unfilled_without_close_substitution(price):
    e = engine()
    e.process_close_signals('2026-10-16', 'BULL')
    e.get_data = lambda sid, day: {'open_price': price, 'close_price': 80}
    e.execute_pending_open('2026-10-19')
    assert e.capital == e.initial_capital and not e.positions and not e.pending_orders
    assert e.execution_events[0]['status'] == 'UNFILLED'
    assert e.execution_events[0]['price'] is None


def test_same_session_execution_is_rejected_and_legacy_behavior_is_preserved():
    e = engine()
    e.process_close_signals('2026-10-16', 'BULL')
    with pytest.raises(ValueError, match='follow signal'):
        e.execute_pending_open('2026-10-16')
    old = engine('legacy_same_close')
    old.process_close_signals('2026-10-19', 'BULL')
    assert old.positions['2330']['cost'] == 80 and not old.pending_orders


def test_new_contract_requires_session_list_and_prohibits_old_result_persistence():
    with pytest.raises(ValueError, match='ordered unique'):
        BacktestEngine(execution_contract='next_session_open_v2', persist_results=False)
    with pytest.raises(ValueError, match='Research execution'):
        BacktestEngine(execution_contract='next_session_open_v2', trading_sessions=['2026-10-19'])
    with pytest.raises(ValueError, match='Research execution'):
        PortfolioBacktestEngine(['macd'], '2026-10-19', execution_contract='next_session_open_v2')


def prepare_run(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, parameters):
            assert 'SELECT DISTINCT trade_date' in str(query)
            return SimpleNamespace(fetchall=lambda: [(datetime.fromisoformat(d),) for d in ('2026-10-16', '2026-10-19', '2026-10-20')])

    e = engine()
    e.engine = SimpleNamespace(connect=Connection)
    monkeypatch.setattr('core.db_helper.get_db_engine', lambda: e.engine)
    e.start_date, e.end_date = '2026-10-16', '2026-10-20'
    e.trading_sessions = ['2026-10-16', '2026-10-19', '2026-10-20']
    e.persist_results, e.persist_to_db = False, False
    e.market_data_reads, e.market_data_read_failures = 0, 0
    e.daily_assets, e.daily_dates = [], []
    e.get_market_trend = lambda day: 'BULL'
    return e


def test_full_single_run_preserves_terminal_signal_as_unexecuted(monkeypatch):
    e = prepare_run(monkeypatch)
    result = e.run(return_metrics=True)
    assert result['execution_contract'] == 'next_session_open_v2'
    assert len(result['execution_events']) == 2
    assert result['unexecuted_end_of_window_signals'][0]['signal_date'] == '2026-10-20'
    assert result['risk_metrics'].get('trade_count').value == 1


def test_portfolio_run_uses_same_entry_and_exit_schedule(monkeypatch):
    e = prepare_run(monkeypatch)
    portfolio = PortfolioBacktestEngine.__new__(PortfolioBacktestEngine)
    portfolio.execution_contract, portfolio.trading_sessions = e.execution_contract, e.trading_sessions
    portfolio.start_date, portfolio.end_date = e.start_date, e.end_date
    portfolio.strategy_names, portfolio.engines = ['macd'], {'macd': e}
    portfolio.initial_capital = e.initial_capital
    portfolio.allocated_capital = {'macd': e.initial_capital}
    portfolio.persist_results, portfolio.persist_to_db = False, False
    result = portfolio.run_portfolio_backtest()
    assert [(x['signal_date'], x['execution_date']) for x in e.execution_events] == [('2026-10-16', '2026-10-19'), ('2026-10-19', '2026-10-20')]
    assert len(result['trades']) == 1
