"""Shared-capital portfolio on synthetic engines (no snapshot). Not investment evidence."""
import pytest

from core.backtest import runner
from core.backtest.costs import CostModel
from core.backtest.portfolio import Portfolio
from core.research.platform.engine_v2 import rule_engine
from core.research.platform.ledger_v2 import reconcile
from core.research.platform.portfolio_v2 import Book, SleeveView, check_weights, run_days, sleeve_engine

SESSIONS = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09']
PRICES = {'2330': [100, 101, 99, 103, 104], '2317': [50, 52, 51, 49, 50], '1101': [30, 30, 31, 32, 31]}


@pytest.fixture(autouse=True)
def no_slippage(monkeypatch):
    monkeypatch.setattr(runner.Config, 'SLIPPAGE_MAX_PIPS', 0.0, raising=False)


def bar(sid, day):
    price = float(PRICES[sid][SESSIONS.index(day)])
    return {'open_price': price, 'close_price': price}


def make(cls, capital, picks, portfolio):
    e = cls.__new__(cls)
    e.execution_contract, e.research_data, e.valuation_policy = 'next_session_open_v2', None, 'legacy'
    e.pending_orders, e.execution_events, e._execution_asof, e.valuation_events = [], [], None, []
    e.initial_capital, e.portfolio = capital, portfolio
    e.costs = CostModel(0.001425, 0.003, 20)
    e._get_max_holdings = lambda day: 3
    e.stop_loss_pct, e.take_profit_pct, e.max_hold_days = .5, .5, 3
    e.mode, e.strategy_obj = 'v30', None
    e.trade_count, e.win_count, e.trades = 0, 0, []
    e.get_data = bar
    e.get_market_trend = lambda day: 'BULL'
    e.find_candidates = lambda day: picks
    return e


def portfolio(members, capital, weights=None):
    book = Book(capital)
    weights = check_weights([m for m, _ in members], weights)
    cls = sleeve_engine(rule_engine('LOT_ENGINE_V1'))
    sleeves = [make(cls, capital * w, picks, SleeveView(book, name)) for (name, picks), w in zip(members, weights)]
    return sleeves, weights, book


def test_single_member_weight_one_equals_the_standalone_engine():
    sleeves, weights, book = portfolio([('a', ['2330', '2317'])], 1_000_000, [1.0])
    result = run_days(sleeves, weights, book, SESSIONS)
    alone = make(rule_engine('LOT_ENGINE_V1'), 1_000_000, ['2330', '2317'], Portfolio(1_000_000))
    nav = []
    for day in SESSIONS:  # BacktestEngine.run's session loop
        alone._apply_corporate_actions(day)
        alone.execute_pending_open(day)
        nav.append([day, alone.capital + sum(alone._mark_position(s, i, day) for s, i in alone.positions.items())])
        alone.process_close_signals(day, 'BULL')
    strip_ids = lambda rows: [{k: v for k, v in row.items() if k not in ('fill_id', 'order_id')} for row in rows]
    assert result['nav'] == nav and strip_ids(sleeves[0].fill_rows()) == strip_ids(alone.fill_rows()) and sleeves[0].trades == alone.trades


def test_cash_contention_overlap_and_no_negative_cash():
    sleeves, weights, book = portfolio([('a', ['2330', '2317']), ('b', ['2330', '1101'])], 2_000_000, [0.5, 0.5])
    result = run_days(sleeves, weights, book, SESSIONS)
    reasons = {(r['sleeve'], r['stock_id'], r['reason']) for r in book.rejections}
    assert ('b', '2330', 'OVERLAP_SKIPPED') in reasons  # a got 2330 first (catalog order)
    owners = {}
    for row in result['positions_daily']:
        if row['stock_id'] != 'CASH':
            assert owners.setdefault((row['date'], row['stock_id']), row['sleeve']) == row['sleeve']
        else:
            assert row['value'] >= 0
    fills = [f for s in sleeves for f in s.fill_rows()]
    assert reconcile(2_000_000, result['nav'], fills, [], [r for r in result['positions_daily']],
                     [{'stock_id': s, 'shares': i['shares']} for s, i in book.positions.items()])['status'] == 'PUBLISHABLE'


def test_cash_contention_is_recorded():
    sleeves, weights, book = portfolio([('a', ['2330']), ('b', ['2317'])], 2_000_000, [0.5, 0.5])
    book.cash = 250_000  # after a's 200,000 buy the shared cash is below b's full 200,000 position budget
    run_days(sleeves, weights, book, SESSIONS[:2])
    assert any(r['reason'] == 'CASH_CONTENTION' and r['sleeve'] == 'b' for r in book.rejections)
    assert book.cash >= 0


def test_duplicate_fill_is_not_booked_twice():
    sleeves, weights, book = portfolio([('a', ['2330'])], 1_000_000, [1.0])
    run_days(sleeves, weights, book, SESSIONS[:2])
    engine, cash = sleeves[0], book.cash
    engine._record_fill(dict(engine.fill_rows()[0]))
    engine.buy('2330', 101.0, SESSIONS[1])
    assert len(engine.fill_rows()) == 1 and book.cash == cash


def test_failed_fill_rolls_back_the_shared_book(monkeypatch):
    sleeves, weights, book = portfolio([('a', ['2330'])], 1_000_000, [1.0])
    monkeypatch.setattr(SleeveView, 'open_position', lambda self, sid, info: (_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError):
        sleeves[0].buy('2330', 100.0, SESSIONS[1])
    assert book.cash == 1_000_000 and not book.positions and sleeves[0].fill_rows() == []


@pytest.mark.parametrize('members, weights', [(['a', 'b'], [0.97, 0.03]), (['a', 'b'], [0.5, 0.6]), (['a', 'a'], None), ([], None),
                                              (['a', 'b'], [0.5]), (['a', 'b'], [True, 0.5])])
def test_invalid_weights_are_refused(members, weights):
    with pytest.raises(ValueError):
        check_weights(members, weights)


@pytest.mark.parametrize('weights', [[float('nan'), 0.5], [float('inf'), 0.5], [float('-inf'), 0.5]])
def test_nonfinite_weights_are_refused(weights):
    with pytest.raises(ValueError):
        check_weights(['a', 'b'], weights)


def test_equal_weights_obey_minimum():
    assert check_weights(list(range(20))) == [0.05] * 20
    with pytest.raises(ValueError):
        check_weights(list(range(21)))


def test_annual_rebalance_resets_budget_base():
    days = ['2025-12-30', '2025-12-31', '2026-01-02']
    sleeves, weights, book = portfolio([('a', [])], 1_000_000, [1.0])
    sleeves[0].get_data = lambda sid, day: None
    result = run_days(sleeves, weights, book, days, 'REBAL_ANNUAL_V1')
    assert sleeves[0].initial_capital == result['nav'][1][1]
    with pytest.raises(ValueError):
        run_days(sleeves, weights, book, days, 'REBAL_MONTHLY')
