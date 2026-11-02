"""Phase 7 V6 audit helpers on synthetic tracks: fill basis, exit bounds, r3/r4 decomposition, read-only output guard."""
import math

import numpy as np
import pytest

from core.backtest.costs import CostModel
from core.research.research_mode import screen_registration
from scripts import research_v6_audit as audit
from scripts import track_b_stats as stats

COST = CostModel(0.00285, 0.006, 40)


def _track():
    daily = [{'date': '2024-01-02', 'nav': 1000.0, 'positions': {'A': 10}, 'stock_receivable': [], 'cash_receivable_claims': [],
              'marks': {'A': 10.0}},
             {'date': '2024-01-03', 'nav': 1000.0, 'positions': {}, 'stock_receivable': [], 'cash_receivable_claims': [], 'marks': {}}]
    notional = 10 * 10.0
    ledger = [{'date': '2024-01-02', 'kind': 'FILL', 'stock_id': 'A', 'side': 'buy', 'notional': 100.0, 'fee': 40, 'tax': 0},
              {'date': '2024-01-03', 'kind': 'ACTION', 'stock_id': 'A', 'action_type': 'reference_change_exit', 'pre_close': 10.0,
               'reference_price': 20.0},
              {'date': '2024-01-03', 'kind': 'FORCED_EXIT', 'stock_id': 'A', 'shares': 10, 'price': 10.0, 'notional': notional,
               'fee': COST.buy_cost(notional), 'tax': int(notional * COST.tax_rate), 'reason': 'loss offset'}]
    return {'result': {'daily': daily, 'ledger': ledger}}


def _bars(close, one_price=False, later=None):
    bar = {'open': close, 'high': close if one_price else close * 1.02, 'low': close if one_price else close * 0.98, 'close': close,
           'volume': 1e6, 'amount': 2e7}
    bars = {'2024-01-03': {'A': bar}}
    if later:
        bars['2024-01-04'] = {'A': {**bar, 'high': later * 1.01, 'low': later * 0.99, 'close': later}}
    return bars


def test_forced_exit_is_never_fill_evidence_and_bounds_use_the_post_action_basis():
    bars = _bars(22.0)
    [row] = audit.exit_rows(_track(), bars, sorted(bars), COST, 'DAY3', 'c', 'stress')
    assert row['fill_basis'] == audit.FILL_BASIS and row['execution_status'] == 'QUANTIFIED_BY_PRICE_BOUNDS'
    assert all(row[k] for k in row if k.startswith('check_'))
    # compared on the post-action basis: reference 20 lies outside the 21.56..22.44 range
    assert row['post_action_equivalent_price'] == 20.0 and row['price_within_limits'] is False and row['volume_cover_ratio'] == 100.0 / 2e7
    inside_bars = _bars(20.2)
    [inside] = audit.exit_rows(_track(), inside_bars, sorted(inside_bars), COST, 'DAY3', 'c', 'stress')
    assert inside['price_within_limits'] is True and inside['fill_basis'] == audit.FILL_BASIS  # in range is still not a fill
    gross = 100.0 * 22.0 / 20.0
    assert math.isclose(audit.exit_delta(row, 'close', COST), (gross - COST.sell_cost(gross)) - (100.0 - row['fee'] - row['tax']))


def test_limit_down_lock_is_uncertified_and_uses_the_first_tradable_session():
    bars = _bars(18.0, one_price=True, later=16.0)
    [row] = audit.exit_rows(_track(), bars, sorted(bars), COST, 'DAY3', 'c', 'stress')
    assert row['sell_side_locked'] and row['execution_status'] == audit.UNCERTIFIED and row['first_tradable_close'] == 16.0
    gross = 100.0 * 16.0 / 20.0
    assert math.isclose(audit.exit_delta(row, 'low', COST), (gross - COST.sell_cost(gross)) - (100.0 - row['fee'] - row['tax']))


def test_adjusted_track_credits_cash_from_its_date_and_pnl_still_sums_to_nav_change():
    track = audit.adjusted_track(_track(), [('2024-01-03', 'A', 5.0)])
    assert [r['nav'] for r in track['result']['daily']] == [1000.0, 1005.0]
    pnl = audit.pnl_components(track)
    assert pnl['components']['cash_credit'] == 5.0 and pnl['traded_notional'] == 100.0
    assert pnl['net_profit'] == -140.0 + (100.0 - 40 - 0) + 5.0  # buy with fee, forced exit net of fee and tax, credit
    assert pnl['top5_share_of_net'] is None  # a non-positive total leaves r4 undefined, never zero


def test_dsr_rederivation_matches_the_screen_formula_and_units():
    x = np.random.default_rng(1).normal(0.0005, 0.01, 724)
    parts = audit.dsr_parts(x, 743, 0.00224)
    assert math.isclose(parts['dsr'], stats.deflated_sharpe(x, 743, 0.00224), rel_tol=1e-9, abs_tol=1e-12)
    assert math.isclose(parts['sr_annual'], stats.sharpe(x), rel_tol=1e-9)
    assert parts['required_sr_annual_for_0_95'] > parts['expected_max_sr_annual']
    assert audit.dsr_parts(x, 1, 0.00224)['expected_max_sr_daily'] == 0.0  # one trial: plain PSR


def test_best_days_share_normal_approximation_tracks_the_observed_share():
    x = np.random.default_rng(2).normal(0.0006, 0.012, 20000)
    parts = audit.days_share_parts(x)
    assert abs(parts['observed'] - parts['normal_approximation']) / parts['normal_approximation'] < 0.1


def test_registered_screen_thresholds_are_unchanged():
    import inspect
    from core.research import research_screen
    assert 'DSR is at most 0.95 using max(research trials, 743)' in screen_registration()['falsification_test']
    assert 'concentration is at least 50%' in screen_registration()['falsification_test']
    source = inspect.getsource(research_screen.screen)  # the thresholds the screen actually applies
    for clause in ("'r3': dsr > .95", "stock_share < .5 and day_share < .5", 'n_trials = max(research_trials, 743)',
                   "regimes[name]['sessions'] >= 60", "'r2': bootstrap['lower'] > 0", "'r1': net_sharpe > 0 and excess_sharpe > 0"):
        assert clause in source, clause


def test_audit_refuses_to_write_into_v6_inputs():
    with pytest.raises(ValueError, match='AUDIT_OUTPUT_INSIDE_V6_INPUTS'):
        audit.audit(audit.RUNS['DAY3'] / 'audit')
