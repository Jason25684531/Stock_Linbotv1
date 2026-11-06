import math

import pytest

from core.research.benchmark_v2 import equal_weight_total_return as v2
from scripts.research_mode_report import benchmark_returns as v1

DAYS = ('2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07')


def bars(closes):
    """closes: stock -> list of closes, one per day in DAYS."""
    return {day: {s: {'close': c[i]} for s, c in closes.items() if c[i] is not None} for i, day in enumerate(DAYS)}


BASE = bars({'1101': [10, 11, 12, 12], '2330': [20, 20, 21, 22], '2603': [5, 5, 5, 6]})


def test_no_actions_equals_v1():
    out = v2(BASE, [])
    assert out['returns'].tolist() == pytest.approx(v1(BASE).tolist(), rel=1e-9)
    assert out['diagnostics']['benchmark_cost'] == 'NONE'


def test_cash_dividend_only_on_ex_date():
    action = {'ex_date': DAYS[2], 'stock_id': '2330', 'type': 'cash', 'cash_per_share': 1.0, 'stock_ratio': 0.0,
              'cash_pay_date': '2025-12-31'}
    plain, total = v2(BASE, [])['returns'], v2(BASE, [action])['returns']
    diff = (total - plain).tolist()
    assert diff[0] == 0 and diff[2] == 0
    assert diff[1] == pytest.approx(1.0 / 20 / 3, rel=1e-12)  # cash / prev close, one third of the equal weight


def test_stock_dividend_scales_shares_without_cash():
    action = {'ex_date': DAYS[1], 'stock_id': '1101', 'type': 'stock', 'cash_per_share': 0.0, 'stock_ratio': 0.1}
    total = v2(BASE, [action])['returns']
    expected = ((11 * 1.1) / 10 - 1 + 0 + 0) / 3
    assert total.iloc[0] == pytest.approx(expected, rel=1e-12)


def test_no_trade_no_cost():
    flat = bars({'1101': [10, 10, 10, 10], '2330': [20, 20, 20, 20]})
    out = v2(flat, [], cost_per_turnover=0.0157)
    assert out['turnover'].tolist() == [0.0, 0.0, 0.0]
    assert out['net_of_cost'].tolist() == out['returns'].tolist()


def test_rebalance_cost_hand_computed():
    two = bars({'1101': [10, 11, 11, 11], '2330': [20, 20, 20, 20]})
    out = v2(two, [], cost_per_turnover=0.0157)
    drift = (0.5 * 1.1 / 1.05, 0.5 / 1.05)
    traded = 0.5 * (abs(0.5 - drift[0]) + abs(0.5 - drift[1]))
    assert out['turnover'].iloc[1] == pytest.approx(traded, rel=1e-12)
    assert out['net_of_cost'].iloc[1] == pytest.approx(out['returns'].iloc[1] - traded * 0.0157, rel=1e-12)


def test_window_after_development_end_refused():
    late = {**BASE, '2026-01-02': BASE[DAYS[-1]]}
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        v2(late, [])
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        v2(BASE, [{'ex_date': '2026-01-05', 'stock_id': '2330', 'type': 'cash', 'cash_per_share': 1.0}])
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        v2(BASE, [], [{'ex_date': '2025-12-30', 'known_date': '2026-01-02', 'stock_id': '2330'}])


def test_reproducible():
    action = {'ex_date': DAYS[2], 'stock_id': '2330', 'type': 'cash', 'cash_per_share': 1.0}
    a, b = v2(BASE, [action], cost_per_turnover=0.01), v2(BASE, [action], cost_per_turnover=0.01)
    assert a['returns'].equals(b['returns']) and a['net_of_cost'].equals(b['net_of_cost']) and a['diagnostics'] == b['diagnostics']


def test_unresolved_and_reference_events_drop_stock_day():
    events = [{'ex_date': DAYS[1], 'stock_id': '2603', 'type': 'reference_change_exit'}]
    out = v2(BASE, events, [{'ex_date': DAYS[2], 'stock_id': '1101', 'missing_terms': ['actual_effective_event_date']}])
    assert out['returns'].iloc[0] == pytest.approx((11 / 10 - 1 + 0) / 2, rel=1e-12)
    assert out['returns'].iloc[1] == pytest.approx((21 / 20 - 1 + 0) / 2, rel=1e-12)
    assert out['diagnostics']['excluded_stock_days'] == 2 and out['diagnostics']['member_stock_days'] == 9


def test_blocked_when_dividend_coverage_low():
    diagnostics = v2(BASE, [])['diagnostics']
    assert diagnostics['status'] == 'BLOCKED'
    assert any(r.startswith('CASH_DIVIDEND_SHARE_BELOW_60PCT') for r in diagnostics['blocked_reasons'])
    assert math.isclose(diagnostics['years']['2025']['cash_dividend_share'], 0.0)
