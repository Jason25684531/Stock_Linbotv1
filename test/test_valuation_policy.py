"""OD-29 valuation policy: legacy stays byte-for-byte what it was; finite_positive_stale never writes an invalid close into equity.

Synthetic engineering checks only (not performance evidence). The legacy fixture was generated with the pre-OD29 engine:
    python test/test_valuation_policy.py   (run once, before the runner change)
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.backtest.costs import CostModel
from core.backtest.portfolio import Portfolio
from core.backtest.runner import BacktestEngine

FIXTURE = Path(__file__).parent / 'fixtures' / 'baseline' / 'valuation_legacy_characterization.json'
DAYS = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21']
NAN, INF = float('nan'), float('inf')


def bars(**override):
    """One stock, bought at the 2nd session open (signal on the 1st); override[day_index] replaces that session's row (None = no row)."""
    rows = {d: {'open_price': 100.0 + i, 'close_price': 100.5 + i, 'volume': 50_000.0} for i, d in enumerate(DAYS)}
    for index, row in override.items():
        rows[DAYS[int(index[1:])]] = row
    return rows


SCENARIOS = {
    'valid': bars(),
    'no_row': bars(d3=None),
    'nan_close_with_volume': bars(d3={'open_price': NAN, 'close_price': NAN, 'volume': 614.0}),
    'nan_close_no_volume': bars(d3={'open_price': NAN, 'close_price': NAN, 'volume': 0.0}),
    'inf_close': bars(d3={'open_price': 103.0, 'close_price': INF, 'volume': 50_000.0}),
    'zero_close': bars(d3={'open_price': 103.0, 'close_price': 0.0, 'volume': 50_000.0}),
    'negative_close': bars(d3={'open_price': 103.0, 'close_price': -1.0, 'volume': 50_000.0}),
    'three_sessions_invalid': bars(d3={'open_price': NAN, 'close_price': NAN, 'volume': 10.0}, d4=None, d5={'open_price': NAN, 'close_price': NAN, 'volume': 0.0}),
    'no_valid_close_since_entry': bars(d1={'open_price': 101.0, 'close_price': NAN, 'volume': 300.0}),
}


def engine(rows, monkeypatch, policy=None):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query, parameters):
            return SimpleNamespace(fetchall=lambda: [(datetime.fromisoformat(d),) for d in DAYS])

    e = BacktestEngine.__new__(BacktestEngine)
    # research_data stub: the end-of-window mark reads the last stored close (NaN included), like NativeResearchData.last_close
    last_close = lambda sid, day: next((r['close_price'] for d, r in sorted(rows.items(), reverse=True) if d <= day and r is not None), None)
    e.execution_contract, e.research_data, e.trading_sessions = 'next_session_open_v2', SimpleNamespace(last_close=last_close, bar=lambda sid, day: rows.get(day)), list(DAYS)
    e.pending_orders, e.execution_events, e._execution_asof = [], [], None
    e.initial_capital = 1_000_000
    e.portfolio, e.costs = Portfolio(e.initial_capital), CostModel(0.001425, 0.003, 20)
    e._apply_execution_slippage = lambda price, side: price
    e._get_max_holdings = lambda day: 3
    e.stop_loss_pct, e.take_profit_pct, e.max_hold_days = .05, .5, 30
    e.mode, e.strategy_obj = 'v30', None
    e.trade_count, e.win_count, e.trades = 0, 0, []
    e.find_candidates = lambda day: ['2330'] if day == DAYS[0] else []
    e.get_data = lambda sid, day: rows.get(day)
    e.engine = SimpleNamespace(connect=Connection)
    monkeypatch.setattr('core.db_helper.get_db_engine', lambda: e.engine)
    e.start_date, e.end_date = DAYS[0], DAYS[-1]
    e.persist_results, e.persist_to_db = False, False
    e.market_data_reads, e.market_data_read_failures = 0, 0
    e.daily_assets, e.daily_dates = [], []
    e.get_market_trend = lambda day: 'BULL'
    if policy is not None:
        e.valuation_policy, e.valuation_events = policy, []
    return e


def outcome(e):
    error = None
    try:
        e.run()
    except Exception as exc:  # legacy raises in calculate_metrics after the session loop on a non-finite curve
        error = type(exc).__name__ + ': ' + str(exc).split('\n')[0][:120]
    return {'error': error, 'daily_assets': [repr(float(v)) for v in e.daily_assets], 'trades': json.loads(json.dumps(e.trades, default=str)),
            'execution_events': json.loads(json.dumps(e.execution_events, default=str))}


@pytest.mark.parametrize('name', sorted(SCENARIOS))
def test_legacy_default_matches_the_pre_change_characterization(name, monkeypatch):
    assert outcome(engine(SCENARIOS[name], monkeypatch)) == json.loads(FIXTURE.read_text(encoding='utf-8'))[name]


@pytest.mark.parametrize('name', sorted(SCENARIOS))
def test_explicit_legacy_policy_equals_default(name, monkeypatch):
    assert outcome(engine(SCENARIOS[name], monkeypatch, 'legacy')) == outcome(engine(SCENARIOS[name], monkeypatch))


@pytest.mark.parametrize('name', sorted(set(SCENARIOS) - {'no_valid_close_since_entry'}))
def test_stale_policy_keeps_equity_finite_and_orders_unchanged(name, monkeypatch):
    legacy, e = outcome(engine(SCENARIOS[name], monkeypatch)), engine(SCENARIOS[name], monkeypatch, 'finite_positive_stale')
    stale = outcome(e)
    assert stale['error'] is None and all(math.isfinite(float(v)) for v in stale['daily_assets'])
    assert stale['execution_events'] == legacy['execution_events'] and stale['trades'] == legacy['trades']  # valuation never changes an order or a fill
    for event in e.valuation_events:  # the carried price is a mark, never a fill
        assert event['type'] == 'STALE_VALUATION' and event['price'] > 0 and math.isfinite(event['price'])
        assert not any(x['execution_date'] == event['date'] and x.get('price') == event['price'] for x in e.execution_events)


def test_valid_closes_value_exactly_like_legacy(monkeypatch):
    legacy, e = outcome(engine(SCENARIOS['valid'], monkeypatch)), engine(SCENARIOS['valid'], monkeypatch, 'finite_positive_stale')
    assert outcome(e) == legacy and e.valuation_events == []


@pytest.mark.parametrize('name, reason', [('no_row', 'NO_BAR_ROW'), ('nan_close_with_volume', 'NO_VALID_CLOSE_WITH_VOLUME'), ('nan_close_no_volume', 'NO_VALID_CLOSE_NO_VOLUME'),
                                          ('inf_close', 'NO_VALID_CLOSE_WITH_VOLUME'), ('zero_close', 'NON_POSITIVE_CLOSE'), ('negative_close', 'NON_POSITIVE_CLOSE')])
def test_one_invalid_session_carries_the_previous_close_and_records_it(name, reason, monkeypatch):
    e = engine(SCENARIOS[name], monkeypatch, 'finite_positive_stale')
    result = outcome(e)
    [event] = e.valuation_events
    shares = int(e.initial_capital * __import__('core.backtest.runner', fromlist=['POSITION_SIZE']).POSITION_SIZE / 101.0)  # sized at the 101.0 fill
    assert event['date'] == DAYS[3] and event['stock_id'] == '2330' and event['reason'] == reason
    assert event['price'] == 102.5 and event['price_source_date'] == DAYS[2] and event['stale_days'] == 1
    assert event['shares'] == shares and event['value'] == event['shares'] * 102.5
    equity = float(result['daily_assets'][3])
    assert event['equity_share'] == pytest.approx(event['value'] / equity, rel=1e-12)


def test_consecutive_invalid_sessions_count_stale_days(monkeypatch):
    e = engine(SCENARIOS['three_sessions_invalid'], monkeypatch, 'finite_positive_stale')
    outcome(e)
    assert [(x['date'], x['stale_days'], x['price_source_date'], x['reason']) for x in e.valuation_events] == [
        (DAYS[3], 1, DAYS[2], 'NO_VALID_CLOSE_WITH_VOLUME'), (DAYS[4], 2, DAYS[2], 'NO_BAR_ROW'), (DAYS[5], 3, DAYS[2], 'NO_VALID_CLOSE_NO_VOLUME')]
    assert {x['price'] for x in e.valuation_events} == {102.5}


def test_no_valid_close_since_entry_blocks_instead_of_writing_zero(monkeypatch):
    e = engine(SCENARIOS['no_valid_close_since_entry'], monkeypatch, 'finite_positive_stale')
    with pytest.raises(ValueError, match='VALUATION_BLOCKED'):
        e.run()
    assert all(math.isfinite(v) for v in e.daily_assets) and len(e.daily_assets) == 1  # nothing invalid was appended


def test_policy_is_validated_and_research_only():
    with pytest.raises(ValueError, match='valuation_policy'):
        BacktestEngine(valuation_policy='carry_anything', persist_results=False)
    with pytest.raises(ValueError, match='research'):
        BacktestEngine(valuation_policy='finite_positive_stale', persist_results=False)


# ----- OD-P5 (Owner rule 2026-10-27): last valid official close while unquoted, dated and flagged stale; no fill; no future price -----

def test_no_order_fills_while_the_stock_is_unquoted(monkeypatch):
    e = engine(SCENARIOS['three_sessions_invalid'], monkeypatch, 'finite_positive_stale')
    outcome(e)
    held, cash = e.positions['2330']['shares'], e.capital
    for day, prev in ((DAYS[3], DAYS[2]), (DAYS[4], DAYS[3]), (DAYS[5], DAYS[4])):  # NaN open, no row, NaN open
        e.pending_orders.append({'side': 'SELL', 'stock_id': '2330', 'signal_date': prev, 'reason': 'EXIT'})
        e.pending_orders.append({'side': 'BUY', 'stock_id': '2330', 'signal_date': prev, 'reason': 'ENTRY'})
        e.execute_pending_open(day)
    unquoted = [x for x in e.execution_events if x['execution_date'] in DAYS[3:6]]
    assert len(unquoted) == 6 and all(x['status'] == 'UNFILLED' and x['price'] is None for x in unquoted)
    assert e.positions['2330']['shares'] == held and e.capital == cash


def test_result_exports_dated_stale_marks_and_never_the_future_resumption_close(monkeypatch):
    rows = SCENARIOS['three_sessions_invalid']
    e = engine(rows, monkeypatch, 'finite_positive_stale')
    result = e.run(return_metrics=True)
    assert result['valuation_policy'] == 'finite_positive_stale' and result['valuation_events'] == e.valuation_events
    assert result['valuation_events'] is not e.valuation_events  # a copy: consumers never mutate engine state
    resumption = rows[DAYS[6]]['close_price']
    for event in result['valuation_events']:
        assert event['price_source_date'] == DAYS[2] < event['date'] and event['stale_days'] >= 1
        assert event['price'] == rows[DAYS[2]]['close_price'] != resumption  # the carried mark is the last official close, never a later one
    assert result['valuation'] == {'status': 'VALUATION_BLOCKED_ACTIONS_UNKNOWN', 'stale_events': 3, 'max_stale_days': 3, 'blocked': ['2330']}


def test_checked_bar_without_a_valid_close_is_stale_marked_and_never_fills(monkeypatch):
    """Reviewer round 2 MAJOR 1: a full bar (traded open, volume, amount) whose close is NaN goes through fill_check and is rejected."""
    full = lambda i, close: {'open_price': 100.0 + i, 'high_price': 101.0 + i, 'low_price': 99.0 + i, 'close_price': close, 'volume': 50_000.0, 'amount': 5e6}
    rows = {**bars(), DAYS[2]: full(2, 102.5), DAYS[3]: full(3, NAN)}
    e = engine(rows, monkeypatch, 'finite_positive_stale')
    outcome(e)
    held = e.positions['2330']['shares']
    e.pending_orders.append({'side': 'SELL', 'stock_id': '2330', 'signal_date': DAYS[2], 'reason': 'EXIT'})
    e.execute_pending_open(DAYS[3])
    event = e.execution_events[-1]
    assert (event['status'], event['reason'], event['price']) == ('UNFILLED', 'REJECTED_NO_TRADE', 103.0) and e.positions['2330']['shares'] == held
    assert [(x['date'], x['reason']) for x in e.valuation_events] == [(DAYS[3], 'NO_VALID_CLOSE_WITH_VOLUME')]


def test_stale_window_with_a_corporate_action_stays_blocked(monkeypatch):
    from core.backtest.execution import stale_valuation_verdict
    e = engine(SCENARIOS['three_sessions_invalid'], monkeypatch, 'finite_positive_stale')
    outcome(e)
    events = e.valuation_events
    assert stale_valuation_verdict([]) == {'status': 'NO_STALE_VALUATION', 'stale_events': 0, 'max_stale_days': 0, 'blocked': []}
    assert stale_valuation_verdict(events, None)['status'] == 'VALUATION_BLOCKED_ACTIONS_UNKNOWN'
    assert stale_valuation_verdict(events, {'2330': [DAYS[2]], '2317': [DAYS[4]]}) == \
        {'status': 'STALE_LAST_OFFICIAL_CLOSE', 'stale_events': 3, 'max_stale_days': 3, 'blocked': []}  # on the source date: already in the mark
    assert stale_valuation_verdict(events, {'2330': [DAYS[4]]}) == \
        {'status': 'VALUATION_BLOCKED_CORPORATE_ACTION', 'stale_events': 3, 'max_stale_days': 3, 'blocked': ['2330']}


if __name__ == '__main__':
    from _pytest.monkeypatch import MonkeyPatch

    if FIXTURE.exists():
        raise SystemExit(f'{FIXTURE} exists; the characterization is generated once with the pre-change engine')
    patch = MonkeyPatch()
    FIXTURE.write_text(json.dumps({name: outcome(engine(rows, patch)) for name, rows in SCENARIOS.items()}, ensure_ascii=False, indent=1), encoding='utf-8')
    patch.undo()
    print('written', FIXTURE)
